# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The binding forms render in the order that settles a shared input, with the check-on-change markup."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from bottle import template

from openfollow.configuration import (
    GAMEPAD_ACTION_BUTTON_FORM_LABELS,
    GAMEPAD_MENU_BUTTON_FORM_LABELS,
    KEYBOARD_ACTION_FORM_LABELS,
    MENU_RESERVED_BUTTONS,
    VALID_BUTTON_NAMES,
    AppConfig,
    ControllerButtonTrigger,
    HotkeyTrigger,
    OscTransmitterConfig,
)
from openfollow.web import server as _server_module  # noqa: F401 - registers the template path

pytestmark = pytest.mark.unit


class _Form(HTMLParser):
    """Selects and inputs in document order, each with its options, plus label texts by ``for``."""

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[dict] = []
        self.labels: dict[str, str] = {}
        self._label_for: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        if tag in ("select", "input"):
            self.controls.append({"tag": tag, "attrs": attr, "options": []})
        elif tag == "option":
            self.controls[-1]["options"].append(attr.get("value", ""))
        elif tag == "label" and attr.get("for"):
            self._label_for = attr["for"]
            self.labels[self._label_for] = ""

    def handle_data(self, data: str) -> None:
        if self._label_for:
            self.labels[self._label_for] += data.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self._label_for = None


def _parse(html: str) -> _Form:
    form = _Form()
    form.feed(html)
    return form


def _gamepad() -> tuple[str, _Form]:
    html = template("partials/gamepad", config=AppConfig(), button_names=sorted(VALID_BUTTON_NAMES))
    return html, _parse(html)


def _named(form: _Form, prefix: str) -> list[dict]:
    return [c for c in form.controls if c["attrs"].get("name", "").startswith(prefix)]


def test_gamepad_buttons_render_in_the_order_that_keeps_a_shared_button() -> None:
    _, form = _gamepad()
    rendered = [c["attrs"]["name"] for c in _named(form, "btn_") if c["tag"] == "select"]
    expected = [name for name, _ in GAMEPAD_ACTION_BUTTON_FORM_LABELS + GAMEPAD_MENU_BUTTON_FORM_LABELS]
    assert rendered == expected


def test_gamepad_labels_are_the_ones_the_notes_name() -> None:
    _, form = _gamepad()
    for name, label in GAMEPAD_ACTION_BUTTON_FORM_LABELS + GAMEPAD_MENU_BUTTON_FORM_LABELS:
        [control] = [c for c in form.controls if c["attrs"].get("name") == name]
        assert form.labels[control["attrs"]["id"]] == label


@pytest.mark.parametrize(
    "name",
    [n for n, _ in GAMEPAD_ACTION_BUTTON_FORM_LABELS + GAMEPAD_MENU_BUTTON_FORM_LABELS]
    + ["move_xy_stick", "marker_fader_stick"],
)
def test_gamepad_binding_is_checked_on_change(name: str) -> None:
    html, form = _gamepad()
    [control] = [c for c in form.controls if c["attrs"].get("name") == name]
    attrs = control["attrs"]
    target_id = attrs["hx-target"].lstrip("#")
    assert attrs["hx-get"] == f"/api/validate/gamepad/{name}"
    assert attrs["hx-trigger"] == "change"
    assert "closest form" in attrs["hx-include"]
    assert attrs["aria-describedby"] == target_id
    assert attrs["aria-invalid"] == "false"
    assert re.search(r'<span id="' + re.escape(target_id) + r'" class="field-error">', html)


def test_gamepad_buttons_offer_one_blank_option() -> None:
    _, form = _gamepad()
    for control in _named(form, "btn_"):
        assert control["options"].count("") == 1, control["attrs"]["name"]


def test_menu_buttons_do_not_offer_the_buttons_that_move_the_highlight() -> None:
    _, form = _gamepad()
    for control in _named(form, "btn_"):
        offered = set(control["options"]) & MENU_RESERVED_BUTTONS
        if control["attrs"]["name"] in dict(GAMEPAD_MENU_BUTTON_FORM_LABELS):
            assert not offered
        else:
            assert offered == MENU_RESERVED_BUTTONS


def test_keyboard_keys_render_in_the_order_that_keeps_a_shared_key() -> None:
    html = template("partials/keyboard", config=AppConfig())
    form = _parse(html)
    keys = [c for c in _named(form, "key_") if c["attrs"]["name"] != "key_move_layout"]
    assert [c["attrs"]["name"] for c in keys] == [name for name, _ in KEYBOARD_ACTION_FORM_LABELS]
    for control, (_, label) in zip(keys, KEYBOARD_ACTION_FORM_LABELS, strict=True):
        assert form.labels[control["attrs"]["id"]] == label


def test_toggle_zones_key_accepts_every_key_the_server_accepts() -> None:
    form = _parse(template("partials/keyboard", config=AppConfig()))
    [control] = [c for c in form.controls if c["attrs"].get("name") == "key_toggle_zones"]
    assert "pattern" not in control["attrs"]


def _trigger_form(trigger: object, overlap: str) -> tuple[str, dict]:
    row = OscTransmitterConfig(id="r1", trigger=trigger)
    kind = trigger.kind
    html = template(
        "partials/osc_binding_trigger_form",
        row=row,
        kind=kind,
        valid_rates=(),
        valid_edges=("press", "release"),
        valid_modifiers=(),
        valid_keys=["", "p", "x"],
        valid_buttons=["", "A", "B"],
        valid_midi_types=(),
        virtual_fader_names=[],
        midi_patches=[],
        binding_overlap=lambda k, value: overlap if value else "",
    )
    field = "trigger.key" if kind == "hotkey" else "trigger.button"
    [control] = [c for c in _parse(html).controls if c["attrs"].get("name") == field]
    return html, control["attrs"]


@pytest.mark.parametrize(
    ("trigger", "field"),
    [(ControllerButtonTrigger(button="B"), "trigger.button"), (HotkeyTrigger(key="x"), "trigger.key")],
)
def test_osc_trigger_on_an_action_input_opens_with_the_caution(trigger: object, field: str) -> None:
    html, attrs = _trigger_form(trigger, "Also Toggle Zone Overlay on the gamepad")
    assert "data-binding-overlap" in attrs
    assert attrs["hx-get"] == f"/api/validate/osc_binding/{field}"
    assert attrs["hx-trigger"] == "change"
    assert 'class="field-caution-msg"' in html
    assert "Also Toggle Zone Overlay on the gamepad" in html


def test_osc_trigger_without_an_overlap_opens_plain() -> None:
    html, attrs = _trigger_form(ControllerButtonTrigger(button="A"), "")
    assert "data-binding-overlap" not in attrs
    assert "field-caution-msg" not in html
