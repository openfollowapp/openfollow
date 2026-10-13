# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Every checkbox in the web UI carries a noun label and an inline action label.

The noun label sits in the field-label slot above the box and names the
setting (``Grid``); the inline label sits beside the box and says what ticking
it does (``Show the grid on the Operator Screen``). Both point at the input with
``for`` / ``id``, which is also what gives the box its accessible name. A box in
a ``role="group"`` takes its noun from the group's ``aria-labelledby`` instead.

Scanned: every template, every bundled script, every video input's settings
fragment, and the markup a template's script builds from strings. A checkbox a
script creates element by element is a list row (the detection masks), which
shows no label and is named after its row with ``aria-label``.
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import AppConfig, OscTransmittersConfig, save_config
from openfollow.video.inputs import get_registry
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

_WEB_DIR = Path(__file__).resolve().parents[1] / "openfollow" / "web"
_TEMPLATES = sorted((_WEB_DIR / "templates").rglob("*.tpl"))
_SCRIPTS = sorted((_WEB_DIR / "static" / "js").glob("*.js"))

_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass
class _Label:
    for_id: str
    start: int
    end: int = -1
    text: str = ""
    wraps_checkbox: bool = False


@dataclass
class _Checkbox:
    attrs: dict[str, str]
    seq: int
    line: int
    group_label_ids: list[str] = field(default_factory=list)


class _CheckboxMarkup(HTMLParser):
    """Checkboxes, the labels around them, and the text of every element with an ``id``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.seq = 0
        self.checkboxes: list[_Checkbox] = []
        self.labels: list[_Label] = []
        self.texts: dict[str, str] = {}
        self.scripts: list[tuple[int, str]] = []
        self._open: list[tuple[str, str, str]] = []  # (tag, id, group aria-labelledby)
        self._open_labels: list[_Label] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.seq += 1
        a = {k: (v or "") for k, v in attrs}
        if tag == "input" and a.get("type") == "checkbox":
            groups = [g for _, _, g in self._open if g]
            self.checkboxes.append(_Checkbox(a, self.seq, self.getpos()[0], groups))
            for label in self._open_labels:
                label.wraps_checkbox = True
        if tag == "label":
            label = _Label(a.get("for", ""), self.seq)
            self.labels.append(label)
            self._open_labels.append(label)
        if tag not in _VOID_TAGS:
            group = a.get("aria-labelledby", "") if a.get("role") == "group" else ""
            self._open.append((tag, a.get("id", ""), group))

    def handle_endtag(self, tag: str) -> None:
        self.seq += 1
        if tag == "label" and self._open_labels:
            self._open_labels.pop().end = self.seq
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][0] == tag:
                del self._open[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._open and self._open[-1][0] == "script":
            self.scripts.append((self.getpos()[0], data))
            return
        for label in self._open_labels:
            label.text += data
        for _, elem_id, _ in self._open:
            if elem_id:
                self.texts[elem_id] = self.texts.get(elem_id, "") + data


def _markup_problems(source: str, where: str) -> list[str]:
    parser = _CheckboxMarkup()
    parser.feed(source)
    problems: list[str] = []
    for cb in parser.checkboxes:
        at = f"{where}:{cb.line}"
        box_id = cb.attrs.get("id", "")
        if not box_id:
            problems.append(f"{at}: checkbox {cb.attrs.get('name', '')!r} has no id for its labels to point at")
            continue
        labels = [lb for lb in parser.labels if lb.for_id == box_id]
        nouns = [lb.text.strip() for lb in labels if lb.end < cb.seq and not lb.wraps_checkbox]
        nouns += [parser.texts.get(g, "").strip() for g in cb.group_label_ids]
        inlines = [lb.text.strip() for lb in labels if lb.start > cb.seq or lb.wraps_checkbox]
        if not any(nouns):
            problems.append(f"{at}: checkbox {box_id!r} has no noun label above it")
        if not any(inlines):
            problems.append(f"{at}: checkbox {box_id!r} has no inline label beside it")
        if any(n and n in inlines for n in nouns):
            problems.append(f"{at}: checkbox {box_id!r} repeats its noun label as its inline label")
    return problems


# Markup a script builds as a string: ``'<input type="checkbox" id="x" ...'``.
_SCRIPT_MARKUP_BOX = re.compile(r"""<input\b[^<]*?type=\\?["']checkbox\\?["'][^<]*""")
_SCRIPT_MARKUP_ID = re.compile(r"""\bid=\\?["']([^"'\\]+)""")
# An element a script creates: ``chk.type = 'checkbox'``.
_SCRIPT_DOM_BOX = re.compile(r"""\b(\w+)\.type\s*=\s*["']checkbox["']""")


def _script_problems(script: str, where: str, first_line: int = 1) -> list[str]:
    problems: list[str] = []
    for match in _SCRIPT_MARKUP_BOX.finditer(script):
        at = f"{where}:{first_line + script.count(chr(10), 0, match.start())}"
        id_match = _SCRIPT_MARKUP_ID.search(match.group(0))
        if id_match is None:
            problems.append(f"{at}: script-built checkbox has no id for its labels to point at")
            continue
        box_id = re.escape(id_match.group(1))
        labels = list(re.finditer(rf"""<label for=\\?["']{box_id}\\?["']>([^<]+)</label>""", script))
        if not any(lb.end() < match.start() for lb in labels):
            problems.append(f"{at}: script-built checkbox {id_match.group(1)!r} has no noun label above it")
        if not any(lb.start() > match.start() for lb in labels):
            problems.append(f"{at}: script-built checkbox {id_match.group(1)!r} has no inline label beside it")
    for match in _SCRIPT_DOM_BOX.finditer(script):
        var = re.escape(match.group(1))
        if not re.search(rf"""\b{var}\.setAttribute\(\s*["']aria-label["']""", script):
            at = f"{where}:{first_line + script.count(chr(10), 0, match.start())}"
            problems.append(f"{at}: script-built row checkbox {match.group(1)!r} is not named with aria-label")
    return problems


def _source_problems(source: str, where: str) -> list[str]:
    parser = _CheckboxMarkup()
    parser.feed(source)
    problems = _markup_problems(source, where)
    for line, script in parser.scripts:
        problems += _script_problems(script, where, line)
    return problems


@pytest.mark.unit
@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda p: p.name)
def test_every_template_checkbox_has_a_noun_and_an_inline_label(path: Path) -> None:
    assert _source_problems(path.read_text(encoding="utf-8"), path.name) == []


@pytest.mark.unit
@pytest.mark.parametrize("path", _SCRIPTS, ids=lambda p: p.name)
def test_every_bundled_script_checkbox_is_labelled(path: Path) -> None:
    assert _script_problems(path.read_text(encoding="utf-8"), path.name) == []


@pytest.mark.unit
@pytest.mark.parametrize("input_id", sorted(get_registry()))
def test_every_video_input_checkbox_has_a_noun_and_an_inline_label(input_id: str) -> None:
    html = get_registry()[input_id].web_ui_html({})
    assert _source_problems(html, f"{input_id} web_ui_html") == []


@pytest.mark.unit
def test_an_inline_label_may_wrap_onto_more_lines() -> None:
    # A fixed-height wrapper lets a label that wraps in a narrow column spill over the next row.
    css = (_WEB_DIR / "templates" / "base.tpl").read_text(encoding="utf-8")
    rule = re.search(r"\.checkbox-wrap\.inline\s*\{([^}]*)\}", css)
    assert rule is not None
    assert "height: auto" in rule.group(1)


# The rule itself, exercised against markup it must reject and accept.

_GOOD_FIELD = """
<div class="field checkbox-field">
    <label for="grid-visible">Grid</label>
    <div class="checkbox-wrap checkbox-field inline">
        <input type="checkbox" id="grid-visible" name="visible">
        <label for="grid-visible">Show the grid on the Operator Screen</label>
    </div>
</div>
"""


@pytest.mark.unit
@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param(
            '<label>Enabled</label><div class="checkbox-wrap"><input type="checkbox" name="enabled"></div>',
            "has no id",
            id="bare-label-above",
        ),
        pytest.param(
            '<input type="checkbox" id="p"><label for="p">Show Preview</label>',
            "has no noun label above it",
            id="inline-label-only",
        ),
        pytest.param(
            '<label for="g">Show Grid</label><input type="checkbox" id="g">',
            "has no inline label beside it",
            id="noun-label-only",
        ),
        pytest.param(
            '<label for="g">Grid</label><input type="checkbox" id="g"><label for="g"> </label>',
            "has no inline label beside it",
            id="empty-inline-label",
        ),
        pytest.param(
            '<label for="g">Grid</label><input type="checkbox" id="g"><label for="g">Grid</label>',
            "repeats its noun label",
            id="same-text-twice",
        ),
        pytest.param(
            '<label for="other">Grid</label><input type="checkbox" id="g"><label for="g">Show the grid</label>',
            "has no noun label above it",
            id="noun-label-for-another-input",
        ),
        pytest.param(
            '<div role="group" aria-labelledby="mods"><label class="inline-checkbox" for="m">'
            '<input type="checkbox" id="m"> Ctrl</label></div>',
            "has no noun label above it",
            id="group-label-missing",
        ),
    ],
)
def test_the_rule_rejects_a_checkbox_without_both_labels(markup: str, expected: str) -> None:
    problems = _source_problems(markup, "t.tpl")
    assert len(problems) == 1
    assert expected in problems[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(_GOOD_FIELD, id="field"),
        pytest.param(
            '<label id="mods">Modifiers</label><div role="group" aria-labelledby="mods">'
            '<label class="inline-checkbox" for="m"><input type="checkbox" id="m"> Ctrl</label></div>',
            id="group",
        ),
    ],
)
def test_the_rule_accepts_a_checkbox_with_both_labels(markup: str) -> None:
    assert _source_problems(markup, "t.tpl") == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("script", "expected"),
    [
        pytest.param(
            'html += \'<label>Enabled</label><input type="checkbox" data-zone-field="enabled">\';',
            "has no id",
            id="markup-without-id",
        ),
        pytest.param(
            'html += \'<input type="checkbox" id="z">\' + \'<label for="z">Use this zone</label>\';',
            "has no noun label above it",
            id="markup-without-noun",
        ),
        pytest.param(
            'html += \'<label for="z">Zone</label>\' + \'<input type="checkbox" id="z">\';',
            "has no inline label beside it",
            id="markup-without-inline",
        ),
        pytest.param(
            "var chk = document.createElement('input');\nchk.type = 'checkbox'; chk.title = 'Enabled';",
            "is not named with aria-label",
            id="row-without-name",
        ),
    ],
)
def test_the_rule_rejects_a_script_built_checkbox_without_labels(script: str, expected: str) -> None:
    problems = _script_problems(script, "t.tpl")
    assert len(problems) == 1
    assert expected in problems[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    "script",
    [
        pytest.param(
            'html += \'<label for="z">Zone</label>\' + \'<input type="checkbox" id="z" \' + '
            "(on ? 'checked' : '') + '>' + '<label for=\"z\">Use this zone</label>';",
            id="markup",
        ),
        pytest.param(
            "chk.type = 'checkbox';\nchk.setAttribute('aria-label', 'Use ' + name.value);",
            id="row",
        ),
        pytest.param("if (el.type === 'checkbox') { out[name] = el.checked; }", id="reading-a-checkbox"),
        pytest.param("form.querySelectorAll('input[type=\"checkbox\"]')", id="selecting-checkboxes"),
    ],
)
def test_the_rule_accepts_a_labelled_script_built_checkbox(script: str) -> None:
    assert _script_problems(script, "t.tpl") == []


# The rendered page: ids are only unique once every section sits on one page,
# with two transmitter rows so the per-row boxes appear twice.


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)
    config_path = str(tmp_path / "config.toml")
    cfg = AppConfig()
    cfg.osc_transmitters = OscTransmittersConfig(
        transmitters=[{"trigger": {"kind": "hotkey", "key": "F1"}}, {"trigger": {"kind": "hotkey", "key": "F2"}}]
    )
    save_config(cfg, config_path)
    with live_on_free_port(
        lambda port: ConfigWebServer(config_path=config_path, host="127.0.0.1", port=port, system_name="TestSystem")
    ) as (_server, base):
        yield base


@pytest.mark.integration
def test_every_checkbox_on_the_rendered_page_is_labelled_and_uniquely_named(live_server) -> None:
    with urllib.request.urlopen(f"{live_server}/", timeout=5) as response:
        page = response.read().decode("utf-8")
    parser = _CheckboxMarkup()
    parser.feed(page)
    boxes = [cb.attrs.get("id", "") for cb in parser.checkboxes]
    ids = re.findall(r"""\sid=["']([^"']+)["']""", page)
    assert sum(i.endswith("-trigger-mod-ctrl") for i in boxes) == 2
    assert len(boxes) >= 30
    assert [i for i in boxes if ids.count(i) != 1] == []
    assert _markup_problems(page, "/") == []
