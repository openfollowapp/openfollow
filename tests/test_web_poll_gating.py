# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Timed polls ask the station only while someone can see what they refresh."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_TEMPLATES = Path(__file__).parents[1] / "openfollow" / "web" / "templates"
_SECTION = "[pollShown(this)], poll-shown[pollShown(this)] from:body"


def _triggers() -> list[tuple[str, str]]:
    found = []
    for path in sorted(_TEMPLATES.rglob("*.tpl")):
        text = path.read_text(encoding="utf-8")
        found += [(path.name, trigger) for trigger in re.findall(r'hx-trigger="([^"]*)"', text)]
    return found


def _clauses(trigger: str) -> list[str]:
    """The comma-separated clauses of a trigger, leaving commas inside a ``[...]`` filter alone."""
    return [c.strip() for c in re.split(r",(?![^\[]*\])", trigger)]


_POLLS = [(name, trigger) for name, trigger in _triggers() if any(c.startswith("every") for c in _clauses(trigger))]


def test_the_scan_finds_the_polls() -> None:
    assert len(_POLLS) >= 15


@pytest.mark.parametrize(("template", "trigger"), _POLLS)
def test_no_poll_runs_without_a_condition(template: str, trigger: str) -> None:
    for clause in _clauses(trigger):
        if clause.startswith("every"):
            assert re.fullmatch(r"every \S+ \[.+\]", clause), f"{template}: a poll runs while nobody looks: {trigger}"


@pytest.mark.parametrize(("template", "trigger"), _POLLS)
def test_a_poll_catches_up_once_it_is_shown_again(template: str, trigger: str) -> None:
    assert any(c.startswith("poll-shown") and c.endswith("from:body") for c in _clauses(trigger)), template


@pytest.mark.parametrize(
    ("trigger", "clauses"),
    [
        ("every 1s [a() && b(x)], poll-shown[a()] from:body", ["every 1s [a() && b(x)]", "poll-shown[a()] from:body"]),
        ("load, every 3s [f(x, y)]", ["load", "every 3s [f(x, y)]"]),
    ],
)
def test_clauses_split_only_outside_a_filter(trigger: str, clauses: list[str]) -> None:
    assert _clauses(trigger) == clauses


@pytest.fixture
def live_server(tmp_path, monkeypatch) -> Iterator[str]:
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)
    with live_on_free_port(
        lambda port: ConfigWebServer(config_path=str(tmp_path / "config.toml"), host="127.0.0.1", port=port)
    ) as (_server, base):
        yield base


def _get(base: str, path: str) -> tuple[int, str, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, "", e.read().decode()


def _opening_tag(body: str, marker: str) -> str:
    start = body.rindex("<", 0, body.index(marker))
    return body[start : body.index(">", start)]


def test_every_page_loads_the_gate_from_the_station(live_server: str) -> None:
    _, _, page = _get(live_server, "/")
    assert re.search(r'<script src="/assets/js/poll-gate\.js\?v=[^"]+"></script>', page)
    status, content_type, script = _get(live_server, "/assets/js/poll-gate.js")
    assert status == 200 and "javascript" in content_type
    assert "root.pollShown = pollShown;" in script


@pytest.mark.parametrize(
    ("marker", "trigger"),
    [
        ('id="statistics-section-content"', f"every 1s {_SECTION}"),
        ('id="statistics-alerts"', f"every 1s {_SECTION}"),
        ('id="marker-catalog-root"', f"load, every 1500ms {_SECTION}"),
        ('id="overview-peers"', f"every 5s {_SECTION}"),
        ('id="diagnostics-live"', f"every 5s {_SECTION}"),
        ('id="controller-slots-content"', f"every 1s {_SECTION}"),
        ('id="mouse3d-status"', f"every 1s {_SECTION}"),
        # Page-wide: nobody can answer a prompt in a hidden tab, and it shows on return.
        ('id="privilege-password-modal"', "load, every 3s [!document.hidden], poll-shown from:body"),
    ],
)
def test_the_main_page_polls_only_what_is_in_view(live_server: str, marker: str, trigger: str) -> None:
    _, _, page = _get(live_server, "/")
    assert f'hx-trigger="{trigger}"' in _opening_tag(page, marker)


@pytest.mark.parametrize(
    ("template", "url"),
    [
        ("gamepad.tpl", 'hx-get="/section/gamepad"'),
        ("detection.tpl", 'hx-get="/section/detection"'),
        ("midi_fader_capture_status.tpl", "/learn/poll"),
        ("osc_midi_capture_status.tpl", "/midi/learn/poll/"),
        ("video_source_latency.tpl", 'hx-get="/section/video_source/latency"'),
    ],
)
def test_a_short_lived_poll_pauses_only_with_the_browser_tab(template: str, url: str) -> None:
    # It runs while something the operator started lasts, so a folded section must not stall it.
    text = (_TEMPLATES / "partials" / template).read_text(encoding="utf-8")
    tag = text[text.rindex("<", 0, text.index(url)) :]
    trigger = re.search(r'hx-trigger="([^"]*)"', tag)
    assert trigger and trigger.group(1).endswith("[!document.hidden], poll-shown from:body")


# -- Script polls: timers that ask the station again ----------------------------------------

_STATIC_JS = _TEMPLATES.parent / "static" / "js"
_DEFINITION = re.compile(r"function\s+(\w+)\s*\(|(?:window\.)?(\w+)\s*=\s*(?:async\s+)?function\s*\(")
_REPEATS = re.compile(
    r"setInterval\((?:\s*(?!function\b)(\w+))?|setTimeout\(\s*(\w+)\s*,|await new Promise\(\(\w+\) => setTimeout\("
)
# Each poll by its file and the function it repeats, and where its gate lives: the function and the
# expression that keeps it from asking while nobody looks. ``None``: deliberately ungated.
_SCRIPT_POLLS: dict[tuple[str, str], tuple[str, str] | None] = {
    # Locked views that wait for the station, with a give-up timed from their start.
    ("base.tpl", "openfollowAwaitRestart"): None,
    ("general.tpl", "openfollowPollUpdate"): None,
    ("base.tpl", "pollPushedSettings"): ("pollPushedSettings", "if (document.hidden && baseline) return;"),
    ("base.tpl", "list"): ("list", "if (!document.hidden) {"),
    ("base.tpl", "poll"): ("poll", "if (!document.hidden) {"),
    ("detect-input.js", "poll"): ("poll", "if (document.hidden) {"),
    ("video_source.tpl", "refresh"): ("refresh", "pollShown(cb)"),
    ("zone_editor.tpl", ""): ("isZoneEditorVisible", "pollShown(section)"),
    ("ground_grid.tpl", ""): (
        "visible",
        "!document.hidden && document.body.contains(canvas) && canvas.getClientRects()",
    ),
    ("path_editor.tpl", ""): ("onScreen", "!document.hidden && q('list').getClientRects()"),
    # The remote's heartbeat and card state, only while the card is open on a visible page.
    ("touch-remote.js", "driveTick"): ("driveTick", "|| document.hidden) return;"),
    ("touch-remote.js", "stateTick"): ("stateTick", "|| document.hidden) return;"),
}


def _bodies(text: str) -> list[tuple[str, int, int]]:
    """Every named function as (name, start, end) of its braces."""
    found = []
    for m in _DEFINITION.finditer(text):
        opening = text.index("{", m.end())
        depth = 0
        for index in range(opening, len(text)):
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            if depth == 0:
                found.append((m.group(1) or m.group(2), opening, index))
                break
    return found


def _enclosing(bodies: list[tuple[str, int, int]], at: int) -> str:
    inside = [(start, name) for name, start, end in bodies if start < at < end]
    return max(inside)[1] if inside else ""


def _script_polls() -> set[tuple[str, str]]:
    """Each timer that runs a function again: a ``setInterval``, a ``setTimeout`` naming the function it
    sits in, and a loop that sleeps on ``setTimeout``. Named by the function repeated, or the one it sits in."""
    found = set()
    for path in [*sorted(_TEMPLATES.rglob("*.tpl")), *sorted(_STATIC_JS.glob("*.js"))]:
        text = path.read_text(encoding="utf-8")
        bodies = _bodies(text)
        for m in _REPEATS.finditer(text):
            around = _enclosing(bodies, m.start())
            if m.group(2) is not None and m.group(2) != around:
                continue  # a one-off delay, not a function asking again
            found.add((path.name, m.group(1) or m.group(2) or around))
    return found


def test_every_script_poll_is_known() -> None:
    # A new timer that asks the station again needs a gate, or a reason it has none.
    assert _script_polls() == set(_SCRIPT_POLLS)


@pytest.mark.parametrize(("site", "gate"), [(k, v) for k, v in _SCRIPT_POLLS.items() if v is not None])
def test_a_script_poll_asks_only_while_someone_looks(site: tuple[str, str], gate: tuple[str, str]) -> None:
    filename, _ = site
    path = next(p for p in [*_TEMPLATES.rglob(filename), *_STATIC_JS.glob(filename)])
    text = path.read_text(encoding="utf-8")
    function, expression = gate
    body = next(text[start:end] for name, start, end in _bodies(text) if name == function)
    assert expression in body, f"{filename}: {function} no longer gates on {expression}"
