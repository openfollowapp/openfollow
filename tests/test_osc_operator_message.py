# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the OSC operator-message adapter.

Covers subscription lifecycle, lenient positional parsing, the ingest
routing filter (broadcast / controlled-marker / drop), the clear handlers,
and the numeric-coercion helpers.
"""

from __future__ import annotations

from typing import Any

import pytest

from openfollow.operator_messages import OperatorMessageStore
from openfollow.osc.operator_message import (
    OperatorMessageOscAdapter,
    _coerce_float,
    _coerce_int,
)

pytestmark = pytest.mark.unit

_MSG = "/message"
_CLEAR = "/message/clear"


class _FakeService:
    """Records subscribe / unsubscribe calls; exposes the handlers."""

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.subscribe_calls: list[str] = []
        self.unsubscribe_calls: list[str] = []

    def subscribe(self, pattern: str, handler: Any) -> None:
        self.subscribe_calls.append(pattern)
        self.handlers[pattern] = handler

    def unsubscribe(self, pattern: str) -> None:
        self.unsubscribe_calls.append(pattern)
        self.handlers.pop(pattern, None)


def _wire(controlled: set[int] | None = None, *, route_by_marker: bool = True):  # noqa: ANN202
    svc = _FakeService()
    store = OperatorMessageStore(clock=lambda: 0.0)
    adapter = OperatorMessageOscAdapter(
        svc,
        store,
        get_controlled_marker_ids=lambda: set(controlled or set()),
        route_by_marker=route_by_marker,
    )
    adapter.start()
    return adapter, svc, store


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_start_subscribes_both_addresses() -> None:
    _, svc, _ = _wire()
    assert set(svc.subscribe_calls) == {_MSG, _CLEAR}


def test_start_is_idempotent() -> None:
    adapter, svc, _ = _wire()
    adapter.start()  # second call
    assert svc.subscribe_calls.count(_MSG) == 1


def test_stop_unsubscribes_both_and_is_idempotent() -> None:
    adapter, svc, _ = _wire()
    adapter.stop()
    assert set(svc.unsubscribe_calls) == {_MSG, _CLEAR}
    svc.unsubscribe_calls.clear()
    adapter.stop()  # no-op
    assert svc.unsubscribe_calls == []


# --------------------------------------------------------------------------- #
# Message ingest – parsing
# --------------------------------------------------------------------------- #


def test_full_message_ingested() -> None:
    _, svc, store = _wire()
    svc.handlers[_MSG](_MSG, "Next cue", "stand by", 0, 8.0)
    m = store.snapshot()[0]
    assert (m.message, m.info, m.marker_id, m.duration_s) == ("Next cue", "stand by", 0, 8.0)


def test_message_only_uses_defaults() -> None:
    _, svc, store = _wire()
    svc.handlers[_MSG](_MSG, "Just a headline")
    m = store.snapshot()[0]
    assert m.info == "" and m.marker_id == 0 and m.duration_s == 0.0


def test_numeric_string_args_coerced() -> None:
    _, svc, store = _wire({3})
    svc.handlers[_MSG](_MSG, "hi", "", "3", "5")
    m = store.snapshot()[0]
    assert m.marker_id == 3 and m.duration_s == pytest.approx(5.0)


def test_no_args_dropped() -> None:
    _, svc, store = _wire()
    svc.handlers[_MSG](_MSG)
    assert store.snapshot() == []


def test_empty_message_dropped() -> None:
    _, svc, store = _wire()
    svc.handlers[_MSG](_MSG, "   ")
    assert store.snapshot() == []


def test_bad_marker_id_drops_packet() -> None:
    _, svc, store = _wire({3})
    svc.handlers[_MSG](_MSG, "hi", "", "notanint")
    assert store.snapshot() == []


def test_negative_marker_id_dropped_not_broadcast() -> None:
    # A negative id drops the packet; it is not clamped to 0.
    _, svc, store = _wire({3})
    svc.handlers[_MSG](_MSG, "hi", "", -3)
    assert store.snapshot() == []


def test_bad_seconds_drops_packet() -> None:
    _, svc, store = _wire()
    svc.handlers[_MSG](_MSG, "hi", "", 0, "abc")
    assert store.snapshot() == []


def test_negative_seconds_clamped_to_forever() -> None:
    _, svc, store = _wire()
    svc.handlers[_MSG](_MSG, "hi", "", 0, -5)
    assert store.snapshot()[0].duration_s == 0.0


# --------------------------------------------------------------------------- #
# Ingest routing
# --------------------------------------------------------------------------- #


def test_broadcast_accepted_on_any_station() -> None:
    _, svc, store = _wire(set())  # no controlled markers
    svc.handlers[_MSG](_MSG, "all stations", "", 0)
    assert len(store.snapshot()) == 1


def test_controlled_marker_accepted() -> None:
    _, svc, store = _wire({3, 4})
    svc.handlers[_MSG](_MSG, "for m3", "", 3)
    assert store.snapshot()[0].marker_id == 3


def test_uncontrolled_marker_dropped() -> None:
    _, svc, store = _wire({3})
    svc.handlers[_MSG](_MSG, "for m9", "", 9)
    assert store.snapshot() == []


def test_routing_reads_live_controlled_ids() -> None:
    controlled: set[int] = set()
    svc = _FakeService()
    store = OperatorMessageStore(clock=lambda: 0.0)
    adapter = OperatorMessageOscAdapter(svc, store, get_controlled_marker_ids=lambda: set(controlled))
    adapter.start()
    svc.handlers[_MSG](_MSG, "m5", "", 5)
    assert store.snapshot() == []  # 5 not controlled yet
    controlled.add(5)
    svc.handlers[_MSG](_MSG, "m5", "", 5)
    assert len(store.snapshot()) == 1  # now accepted


def test_route_by_marker_off_accepts_uncontrolled_marker() -> None:
    # Routing off: a marker-keyed message is accepted even though this station
    # controls no such marker – every station shows it.
    _, svc, store = _wire({3}, route_by_marker=False)
    svc.handlers[_MSG](_MSG, "for m9", "", 9)
    assert store.snapshot()[0].marker_id == 9


# --------------------------------------------------------------------------- #
# Clear handlers
# --------------------------------------------------------------------------- #


def test_clear_no_args_clears_all() -> None:
    _, svc, store = _wire({3})
    svc.handlers[_MSG](_MSG, "a", "", 0)
    svc.handlers[_MSG](_MSG, "b", "", 3)
    svc.handlers[_CLEAR](_CLEAR)
    assert store.snapshot() == []


def test_clear_with_id_clears_that_marker() -> None:
    _, svc, store = _wire({3})
    svc.handlers[_MSG](_MSG, "a", "", 0)
    svc.handlers[_MSG](_MSG, "b", "", 3)
    svc.handlers[_CLEAR](_CLEAR, 3)
    assert [m.marker_id for m in store.snapshot()] == [0]


def test_clear_bad_id_ignored() -> None:
    _, svc, store = _wire()
    svc.handlers[_MSG](_MSG, "a", "", 0)
    svc.handlers[_CLEAR](_CLEAR, "notanint")
    assert len(store.snapshot()) == 1  # nothing cleared


def test_clear_negative_id_ignored() -> None:
    # A negative clear id is ignored; it is not clamped to 0.
    _, svc, store = _wire()
    svc.handlers[_MSG](_MSG, "a", "", 0)
    svc.handlers[_CLEAR](_CLEAR, -1)
    assert len(store.snapshot()) == 1  # broadcasts NOT cleared


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [
        (3, 3),
        (3.9, 3),
        ("3", 3),
        ("3.0", 3),
        ("  4 ", 4),
        (True, None),
        (float("inf"), None),
        ("nan-ish", None),
        ("", None),
        (object(), None),
    ],
)
def test_coerce_int(value: object, expected: int | None) -> None:
    assert _coerce_int(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (3, 3.0),
        (3.5, 3.5),
        ("3.5", 3.5),
        (True, None),
        (float("inf"), None),
        ("abc", None),
        (object(), None),
    ],
)
def test_coerce_float(value: object, expected: float | None) -> None:
    assert _coerce_float(value) == expected
