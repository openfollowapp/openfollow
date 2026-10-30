# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""OSC path commands: parsing, queueing, and subscribing on the shared service."""

from __future__ import annotations

from typing import Any

import pytest

from openfollow.osc.paths import MAX_PENDING, MarkerPath, PathActive, PathOscAdapter

pytestmark = pytest.mark.unit


class _Service:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.unsubscribed: list[str] = []

    def subscribe(self, pattern: str, handler: Any) -> None:
        self.handlers[pattern] = handler

    def unsubscribe(self, pattern: str) -> None:
        self.unsubscribed.append(pattern)
        self.handlers.pop(pattern, None)


def _adapter() -> tuple[PathOscAdapter, _Service]:
    service = _Service()
    adapter = PathOscAdapter(service)  # type: ignore[arg-type]
    adapter.start()
    return adapter, service


class TestLifecycle:
    def test_start_and_stop_subscribe_and_unsubscribe_both_addresses_once(self) -> None:
        adapter, service = _adapter()
        assert set(service.handlers) == {"/path/*/active", "/marker/*/path"}
        adapter.start()
        adapter.stop()
        adapter.stop()
        assert sorted(service.unsubscribed) == ["/marker/*/path", "/path/*/active"]


class TestActive:
    @pytest.mark.parametrize(
        ("arg", "want"),
        [
            (1, True),
            (0, False),
            (0.0, False),
            (2.5, True),
            (True, True),
            (False, False),
            ("on", True),
            (" OFF ", False),
        ],
    )
    def test_a_path_is_switched_on_or_off(self, arg: object, want: bool) -> None:
        adapter, service = _adapter()
        service.handlers["/path/*/active"]("/path/Cross/active", arg)
        assert adapter.take() == [PathActive("Cross", want)]

    @pytest.mark.parametrize("args", [(), (1, 0), ("maybe",), (float("nan"),), (None,)])
    def test_anything_but_one_switch_argument_is_ignored(self, args: tuple[object, ...]) -> None:
        adapter, service = _adapter()
        service.handlers["/path/*/active"]("/path/cross/active", *args)
        assert adapter.take() == []

    @pytest.mark.parametrize("address", ["/path//active", "/path/a/b/active", "/path/active"])
    def test_an_address_of_another_shape_is_ignored(self, address: str) -> None:
        adapter, service = _adapter()
        service.handlers["/path/*/active"](address, 1)
        assert adapter.take() == []


class TestMarker:
    def test_a_name_locks_the_marker_onto_that_path(self) -> None:
        adapter, service = _adapter()
        service.handlers["/marker/*/path"]("/marker/3/path", " cross ")
        assert adapter.take() == [MarkerPath(3, "cross")]

    @pytest.mark.parametrize("args", [(), ("",), ("  ",)])
    def test_no_name_releases_the_marker(self, args: tuple[object, ...]) -> None:
        adapter, service = _adapter()
        service.handlers["/marker/*/path"]("/marker/3/path", *args)
        assert adapter.take() == [MarkerPath(3, None)]

    @pytest.mark.parametrize(
        ("address", "args"),
        [
            ("/marker/x/path", ("cross",)),
            ("/marker/3/4/path", ("cross",)),
            ("/marker/3/path", (5,)),
            ("/marker/3/path", ("a", "b")),
        ],
    )
    def test_anything_else_is_ignored(self, address: str, args: tuple[object, ...]) -> None:
        adapter, service = _adapter()
        service.handlers["/marker/*/path"](address, *args)
        assert adapter.take() == []


def test_commands_come_out_once_in_the_order_they_arrived() -> None:
    adapter, service = _adapter()
    service.handlers["/path/*/active"]("/path/a/active", 1)
    service.handlers["/marker/*/path"]("/marker/2/path", "a")
    assert adapter.take() == [PathActive("a", True), MarkerPath(2, "a")]
    assert adapter.take() == []


def test_a_queue_nobody_takes_keeps_only_the_newest_commands() -> None:
    adapter, service = _adapter()
    for marker_id in range(1, MAX_PENDING + 6):
        service.handlers["/marker/*/path"](f"/marker/{marker_id}/path", "a")
    taken = adapter.take()
    assert [command.marker_id for command in taken] == list(range(6, MAX_PENDING + 6))  # type: ignore[union-attr]
    assert adapter.take() == []
