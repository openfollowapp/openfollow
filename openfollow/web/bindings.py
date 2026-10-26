# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Binding clashes on the input forms, and OSC triggers that share an action's input.

The validate route asks :func:`check_binding` what an edited field takes from
the rest of its form; ``static/js/binding-steal.js`` only applies the answer.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from openfollow.binding_conflicts import settle_duplicates
from openfollow.button_names import ButtonNameStyle, button_label
from openfollow.configuration import (
    GAMEPAD_ACTION_BUTTON_FORM_LABELS,
    GAMEPAD_MENU_BUTTON_FORM_LABELS,
    KEYBOARD_ACTION_FORM_LABELS,
    MOUSE3D_BUTTON_FORM_LABELS,
    ControllerConfig,
)
from openfollow.web.button_icons import button_html
from openfollow.web.labels import pretty_label

CONTROLLER_SECTIONS = frozenset({"controller", "gamepad", "keyboard", "mouse"})
STICK_FIELD_LABELS = {"move_xy_stick": "Move X/Y", "marker_fader_stick": "Marker fader stick"}
STICK_LABELS = {"left": "Left Stick", "right": "Right Stick", "left_y": "Left Stick Y", "right_y": "Right Stick Y"}


@dataclass(frozen=True)
class _Group:
    labels: dict[str, str]
    unbound: Any
    parse: Callable[[str], Any]
    display: Callable[[Any, ButtonNameStyle], str]
    form_unbound: str = ""
    # The value as HTML (a button symbol); the escaped ``display`` when unset.
    display_html: Callable[[Any, ButtonNameStyle], str] | None = None


def _parse_index(raw: str) -> int:
    try:
        return int(raw) if raw.strip() else -1
    except ValueError:
        return -1


_CONTROLLER_GROUPS = (
    *(
        _Group(
            dict(labels),
            "",
            str.strip,
            lambda value, style: button_label(value, style, short=False),
            display_html=button_html,
        )
        for labels in (GAMEPAD_ACTION_BUTTON_FORM_LABELS, GAMEPAD_MENU_BUTTON_FORM_LABELS)
    ),
    _Group(dict(KEYBOARD_ACTION_FORM_LABELS), "", str.strip, lambda value, _style: pretty_label(value)),
)
_MOUSE3D_GROUP = _Group(dict(MOUSE3D_BUTTON_FORM_LABELS), -1, _parse_index, lambda index, _style: f"Button {index}")


@dataclass(frozen=True)
class BindingCheck:
    """What an edited binding does to the rest of its form.

    ``note`` goes on the edited field (``note_html`` is it as markup, with
    any button symbol), ``error`` blocks Save, ``moved`` lists the fields that
    lost their input (for the ``bindingChecked`` event), and ``recheck``
    names fields whose own check may have changed.
    """

    note: str = ""
    note_html: str = ""
    error: str = ""
    moved: tuple[dict[str, str], ...] = ()
    recheck: tuple[str, ...] = ()

    def event(self, field: str) -> dict[str, Any]:
        return {"bindingChecked": {"field": field, "moved": list(self.moved), "recheck": list(self.recheck)}}


def _moved(field: str, unbound: str, text_html: str, kept_by: str, lost_text: str) -> dict[str, str]:
    note = (
        f'<span class="field-warn-msg" role="status" aria-live="polite" data-moved-to="{html.escape(kept_by)}"'
        f' data-lost-text="{html.escape(lost_text)}">{text_html}</span>'
    )
    return {"field": field, "unbound": unbound, "html": note}


def _check_group(group: _Group, field: str, form: Mapping[str, str], style: ButtonNameStyle) -> BindingCheck:
    values = {name: group.parse(form.get(name, "") or "") for name in group.labels}
    moves = [
        m
        for m in settle_duplicates(values, list(group.labels), unbound=group.unbound, prefer=(field,))
        if m.kept_by == field
    ]
    if not moves:
        return BindingCheck()
    shown = group.display(moves[0].value, style)
    shown_html = group.display_html(moves[0].value, style) if group.display_html is not None else html.escape(shown)
    taken_from = ", ".join(group.labels[m.field] for m in moves)
    return BindingCheck(
        note=f"{shown} taken from {taken_from}",
        note_html=f"{shown_html} taken from {html.escape(taken_from)}",
        moved=tuple(
            _moved(
                m.field,
                group.form_unbound,
                f"{shown_html} moved to {html.escape(group.labels[field])}",
                field,
                f"{shown} was taken",
            )
            for m in moves
        ),
    )


def _check_sticks(field: str, form: Mapping[str, str]) -> BindingCheck:
    move = form.get("move_xy_stick", "") or ""
    fader = form.get("marker_fader_stick", "") or ""
    if field == "marker_fader_stick":
        if fader in STICK_LABELS and fader == f"{move}_y":
            return BindingCheck(error=f"{STICK_LABELS[move]} moves the marker (Move X/Y).")
        return BindingCheck()
    if fader in STICK_LABELS and fader == f"{move}_y":
        shown = STICK_LABELS[fader]
        return BindingCheck(
            note=f"{shown} taken from {STICK_FIELD_LABELS['marker_fader_stick']}",
            moved=(
                _moved(
                    "marker_fader_stick", "", html.escape(f"{shown} moved to Move X/Y"), field, f"{shown} was taken"
                ),
            ),
        )
    # A fader error raised against the stick Move X/Y just left no longer holds.
    return BindingCheck(recheck=("marker_fader_stick",) if fader else ())


def check_binding(
    section: str, field: str, form: Mapping[str, str], style: ButtonNameStyle = ButtonNameStyle.STANDARD
) -> BindingCheck | None:
    """The clash an edit of ``field`` makes on ``section``'s form, or ``None`` if it is no binding.

    ``style`` names the gamepad buttons in the notes.
    """
    if section == "mouse3d":
        return _check_group(_MOUSE3D_GROUP, field, form, style) if field in _MOUSE3D_GROUP.labels else None
    if section not in CONTROLLER_SECTIONS:
        return None
    if field in STICK_FIELD_LABELS:
        return _check_sticks(field, form)
    for group in _CONTROLLER_GROUPS:
        if field in group.labels:
            return _check_group(group, field, form, style)
    return None


def osc_trigger_overlap(controller: ControllerConfig, kind: str, value: str) -> str:
    """The caution for an OSC trigger on an input a gamepad or keyboard action also uses, else ``""``.

    Menu buttons are left out: OSC triggers never fire while a menu is open.
    """
    if not value:
        return ""
    if kind == "controller_button" and controller.enabled:
        labels, device = GAMEPAD_ACTION_BUTTON_FORM_LABELS, "gamepad"
    elif kind == "hotkey" and controller.keyboard_enabled:
        labels, device = KEYBOARD_ACTION_FORM_LABELS, "keyboard"
    else:
        return ""
    for name, label in labels:
        if getattr(controller, name) == value:
            return f"Also '{label}' on {device} input"
    return ""


def osc_row_overlap(controller: ControllerConfig, trigger: Any) -> str:
    """:func:`osc_trigger_overlap` for a stored OSC trigger, whatever its kind."""
    kind = getattr(trigger, "kind", "")
    return osc_trigger_overlap(controller, kind, getattr(trigger, "key" if kind == "hotkey" else "button", ""))
