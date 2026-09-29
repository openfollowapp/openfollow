# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What an edited binding takes from its form, and OSC triggers on an action's input."""

from __future__ import annotations

import pytest

from openfollow.configuration import ControllerConfig
from openfollow.web.bindings import check_binding, osc_trigger_overlap

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("section", "field"),
    [("gamepad", "deadzone"), ("camera", "fov"), ("mouse3d", "map_pan_x"), ("keyboard", "key_move_layout")],
)
def test_a_field_that_is_no_binding_is_not_checked(section: str, field: str) -> None:
    assert check_binding(section, field, {field: "x"}) is None


@pytest.mark.parametrize("section", ["controller", "gamepad", "keyboard", "mouse"])
def test_every_controller_section_checks_its_bindings(section: str) -> None:
    check = check_binding(section, "btn_reset", {"btn_reset": "Y", "btn_toggle_help": "Y"})
    assert [m["field"] for m in check.moved] == ["btn_toggle_help"]


def test_a_button_is_only_taken_within_its_group() -> None:
    form = {"btn_menu_confirm": "X", "btn_reset": "X", "key_reset": "X"}
    assert check_binding("gamepad", "btn_menu_confirm", form).moved == ()


def test_a_duplicate_the_edit_did_not_make_is_left_alone() -> None:
    form = {"btn_reset": "A", "btn_toggle_help": "B", "btn_toggle_zones": "B"}
    assert check_binding("gamepad", "btn_reset", form).moved == ()


def test_keys_are_compared_without_edge_whitespace() -> None:
    check = check_binding("keyboard", "key_reset", {"key_reset": " h ", "key_toggle_help": "h"})
    assert check.note == "H taken from Toggle Help"


@pytest.mark.parametrize("raw", ["abc", "", "  "])
def test_a_mouse3d_index_that_is_no_number_takes_nothing(raw: str) -> None:
    check = check_binding("mouse3d", "btn_reset", {"btn_reset": raw, "btn_next_marker": "0"})
    assert check.moved == ()


def test_moving_the_marker_off_a_stick_with_no_fader_rechecks_nothing() -> None:
    check = check_binding("gamepad", "move_xy_stick", {"move_xy_stick": "right", "marker_fader_stick": ""})
    assert check == check_binding("gamepad", "move_xy_stick", {"move_xy_stick": "right"})
    assert check.recheck == ()


@pytest.mark.parametrize("field", ["marker_fader_stick", "move_xy_stick"])
def test_an_unknown_stick_is_no_clash(field: str) -> None:
    check = check_binding("gamepad", field, {"move_xy_stick": "bogus", "marker_fader_stick": "bogus_y"})
    assert (check.error, check.moved) == ("", ())


def test_a_fader_on_the_other_stick_is_no_error() -> None:
    check = check_binding("gamepad", "marker_fader_stick", {"marker_fader_stick": "right_y", "move_xy_stick": "left"})
    assert check.error == ""


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("controller_button", "RT", "Also Move Z+ on the gamepad"),
        ("controller_button", "A", ""),
        ("controller_button", "", ""),
        ("hotkey", "Tab", "Also Next Marker on the keyboard"),
        ("hotkey", "", ""),
        ("midi_message", "B", ""),
    ],
)
def test_osc_trigger_overlap_names_the_action_sharing_the_input(kind: str, value: str, expected: str) -> None:
    assert osc_trigger_overlap(ControllerConfig(), kind, value) == expected


@pytest.mark.parametrize(
    ("switch", "kind", "value"),
    [("enabled", "controller_button", "B"), ("keyboard_enabled", "hotkey", "x")],
)
def test_osc_trigger_overlap_ignores_a_switched_off_device(switch: str, kind: str, value: str) -> None:
    assert osc_trigger_overlap(ControllerConfig(**{switch: False}), kind, value) == ""
