# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Parked markers: the predicate, the selection that never rests on one, and the keyboard's routing."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from openfollow.configuration import AppConfig, DetectionConfig
from openfollow.input.parking import is_parked, rehome_selection, select, set_parked
from openfollow.psn.marker import Marker
from openfollow.runtime import app_modes

pytestmark = pytest.mark.unit


def _app(controlled: list[int], selected: int | None, parked: list[int] | None = None) -> SimpleNamespace:
    app = SimpleNamespace(_controlled_ids=list(controlled), _selected_id=selected, _parked_ids=frozenset())
    if parked:
        set_parked(app, parked)
    return app


@pytest.mark.parametrize(
    ("parked", "marker_id", "expected"),
    [({3}, 3, True), ({3}, 4, False), (set(), 3, False), ({3}, None, False)],
)
def test_is_parked(parked: set[int], marker_id: int | None, expected: bool) -> None:
    assert is_parked(SimpleNamespace(_parked_ids=frozenset(parked)), marker_id) is expected


def test_an_app_without_a_parked_set_parks_nothing() -> None:
    assert is_parked(SimpleNamespace(), 3) is False


@pytest.mark.parametrize(
    ("controlled", "selected", "parked", "expected"),
    [
        ([1, 2, 3], 2, [2], 3),
        ([1, 2, 3], 3, [3], 1),
        ([1, 2, 3], 2, [2, 3], 1),
        ([1, 2, 3], 1, [1, 2, 3], None),
        ([1, 2, 3], 1, [2], 1),
        ([1, 2, 3], None, [1], 2),
        ([1, 2, 3], 9, [], 1),
        ([], None, [], None),
    ],
)
def test_parking_moves_the_selection_to_the_next_unparked_marker(
    controlled: list[int], selected: int | None, parked: list[int], expected: int | None
) -> None:
    app = _app(controlled, selected)
    set_parked(app, parked)
    assert app._selected_id == expected


def test_unparking_with_nothing_selected_selects_the_first_unparked_marker() -> None:
    app = _app([1, 2, 3], 1, [1, 2, 3])
    assert app._selected_id is None
    set_parked(app, [1])
    assert app._selected_id == 2


def test_rehome_leaves_an_unparked_selection_alone() -> None:
    app = _app([1, 2, 3], 3, [1])
    rehome_selection(app)
    assert app._selected_id == 3


def test_set_parked_logs_each_change(caplog: pytest.LogCaptureFixture) -> None:
    app = _app([1, 2, 3], 1)
    with caplog.at_level(logging.INFO, logger="openfollow.input.parking"):
        set_parked(app, [2, 3])
        set_parked(app, [3])
    assert [r.getMessage() for r in caplog.records] == ["Marker 2 parked", "Marker 3 parked", "Marker 2 unparked"]


@pytest.mark.parametrize(
    ("selected", "direction", "parked", "expected"),
    [
        (1, +1, [2], 3),
        (3, -1, [2], 1),
        (1, +1, [2, 3], 1),
        (3, +1, [1], 2),
        (None, +1, [1], 2),
        (None, -1, [3], 2),
    ],
)
def test_cycling_steps_over_parked_markers(
    selected: int | None, direction: int, parked: list[int], expected: int
) -> None:
    app = SimpleNamespace(_controlled_ids=[1, 2, 3], _selected_id=selected, _parked_ids=frozenset(parked))
    app_modes.cycle_marker(app, direction)
    assert app._selected_id == expected


def test_cycling_with_every_marker_parked_selects_nothing() -> None:
    app = _app([1, 2, 3], 1, [1, 2, 3])
    app_modes.cycle_marker(app, +1)
    app_modes.cycle_marker(app, -1)
    assert app._selected_id is None


def _keyboard_app(selected: int, parked: list[int]) -> SimpleNamespace:
    class _KeyboardHandler:
        def on_key_down(self, _event: object) -> None:
            pass

    app = SimpleNamespace(
        _input_manager=SimpleNamespace(
            keyboard_handler=_KeyboardHandler(), mouse_handler=SimpleNamespace(keep_grab=lambda _marker_id: None)
        ),
        _controlled_ids=[10, 20, 30],
        _selected_id=selected,
        _parked_ids=frozenset(parked),
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app._normalize_key = app_modes.normalize_key
    return app


def test_a_digit_never_selects_a_parked_marker() -> None:
    app = _keyboard_app(10, [20])
    app_modes.on_key_down(app, {"key": "2"})
    assert app._selected_id == 10
    app_modes.on_key_down(app, {"key": "3"})
    assert app._selected_id == 30


def test_the_reset_key_never_reaches_a_parked_marker() -> None:
    cfg = AppConfig()
    cfg.controller.key_reset = "x"
    marker = Marker(7, "M7")
    marker.set_pos(1.0, 1.0, 1.0)
    app = SimpleNamespace(
        _config=cfg,
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _controlled_ids=[7],
        _selected_id=7,
        _parked_ids=frozenset({7}),
        _server=SimpleNamespace(get_marker=lambda _id: marker),
    )
    app._get_default_marker_position = lambda: (5.0, 5.0, 5.0)
    app_modes.handle_key_press(app, "x")
    assert marker.pos == (1.0, 1.0, 1.0)


def _tracking(
    enabled: bool = True, pin_mode: str = "replace", pin_marker_id: int = -1, *, feature: bool = True
) -> AppConfig:
    cfg = AppConfig(detection=DetectionConfig(enabled=enabled, pin_mode=pin_mode, pin_marker_id=pin_marker_id))
    if feature:
        cfg.ui.shown_features = [*cfg.ui.shown_features, "detection"]
    return cfg


def test_a_park_that_moves_the_selection_makes_a_following_pin_wait() -> None:
    app = _app([1, 2, 3], 1)
    app._config = _tracking()
    set_parked(app, [1])
    assert app._pin_waits_for_selection is True


@pytest.mark.parametrize(
    "cfg",
    [
        _tracking(enabled=False),
        _tracking(pin_mode="assist"),
        _tracking(pin_marker_id=2),
        _tracking(feature=False),
    ],
    ids=["tracking-off", "ai-assisted", "fixed-follow-marker", "feature-off"],
)
def test_a_park_move_holds_back_only_a_running_fully_automatic_pin_that_follows_the_selection(cfg: AppConfig) -> None:
    app = _app([1, 2, 3], 1)
    app._config = cfg
    set_parked(app, [1])
    assert app._selected_id == 2
    assert getattr(app, "_pin_waits_for_selection", False) is False


@pytest.mark.parametrize(
    ("selected", "parked"),
    [(None, [1]), (2, [1]), (1, [])],
    ids=["no-selection-yet", "another-marker-parks", "nothing-parks"],
)
def test_a_park_that_leaves_the_selection_alone_never_makes_the_pin_wait(
    selected: int | None, parked: list[int]
) -> None:
    app = _app([1, 2, 3], selected)
    set_parked(app, parked)
    assert getattr(app, "_pin_waits_for_selection", False) is False


@pytest.mark.parametrize("how", ["cycle", "digit", "select"])
def test_an_operator_selection_wakes_the_pin(how: str) -> None:
    app = _keyboard_app(10, [])
    app._config = _tracking()
    set_parked(app, [10])
    assert app._pin_waits_for_selection is True
    if how == "cycle":
        app_modes.cycle_marker(app, +1)
    elif how == "digit":
        app_modes.on_key_down(app, {"key": "3"})
    else:
        select(app, 30)
    assert app._pin_waits_for_selection is False
