# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Session slot assignment: port order at startup, missing slots after."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from openfollow.input.controller_slots import (
    CONNECTED,
    MISSING,
    RESERVED,
    ControllerSlotTable,
    LiveController,
)

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def pad(local_id: int, key: str | None, name: str = "Pad") -> LiveController:
    return LiveController(kind="gamepad", local_id=local_id, key=key, name=name)


def puck(local_id: int, key: str | None, name: str = "3D Mouse") -> LiveController:
    return LiveController(kind="mouse3d", local_id=local_id, key=key, name=name)


def frozen(*live: LiveController) -> ControllerSlotTable:
    table = ControllerSlotTable(clock=_Clock())
    table.update(live, settled=True)
    assert not table.seeding
    return table


def layout(table: ControllerSlotTable) -> list[tuple[str, int | None, str]]:
    return [(s.kind, s.local_id, s.state) for s in table.slots]


# -- seeding -----------------------------------------------------------------


def test_startup_order_follows_the_ports_not_the_probe_order() -> None:
    table = frozen(pad(0, "usb:h:2"), pad(1, "usb:h:1"))
    assert layout(table) == [("gamepad", 1, CONNECTED), ("gamepad", 0, CONNECTED)]


def test_a_puck_and_a_pad_interleave_by_port() -> None:
    table = frozen(pad(0, "usb:h:1"), puck(0, "usb:h:2"), pad(1, "usb:h:3"))
    assert [s.kind for s in table.slots] == ["gamepad", "mouse3d", "gamepad"]


def test_ports_sort_numerically() -> None:
    table = frozen(pad(0, "usb:h:1.10"), pad(1, "usb:h:1.2"))
    assert [s.local_id for s in table.slots] == [1, 0]


def test_keyless_controllers_come_last_in_todays_order() -> None:
    table = frozen(pad(7, None), pad(3, None), puck(5, None), pad(9, "usb:h:1"))
    assert layout(table) == [
        ("gamepad", 9, CONNECTED),
        ("mouse3d", 5, CONNECTED),
        ("gamepad", 3, CONNECTED),
        ("gamepad", 7, CONNECTED),
    ]


def test_two_controllers_on_one_usb_device_share_nothing() -> None:
    table = frozen(pad(1, "usb:h:1"), pad(0, "usb:h:1"))
    assert [(s.local_id, s.key) for s in table.slots] == [(0, "usb:h:1"), (1, None)]


def test_seeding_re_sorts_every_frame_until_settled() -> None:
    clock = _Clock()
    table = ControllerSlotTable(clock=clock)
    table.update([pad(0, "usb:h:2")], settled=False)
    assert table.seeding
    table.update([pad(0, "usb:h:2"), puck(0, "usb:h:1")], settled=False)
    assert [s.kind for s in table.slots] == ["mouse3d", "gamepad"]
    table.update([pad(0, "usb:h:2")], settled=False)
    assert [s.kind for s in table.slots] == ["gamepad"]
    table.update([pad(0, "usb:h:2")], settled=True)
    assert not table.seeding


def test_seeding_ends_at_the_deadline_without_a_settled_scan() -> None:
    clock = _Clock()
    table = ControllerSlotTable(clock=clock, seed_deadline_s=3.0)
    table.update([pad(0, "usb:h:1")], settled=False)
    clock.now += 2.9
    table.update([pad(0, "usb:h:1")], settled=False)
    assert table.seeding
    clock.now += 0.1
    table.update([pad(0, "usb:h:1")], settled=False)
    assert not table.seeding


# -- frozen ------------------------------------------------------------------


def test_a_departed_controller_leaves_its_slot_missing() -> None:
    table = frozen(pad(0, "usb:h:1", "GameSir"), pad(1, "usb:h:2"))
    assert table.update([pad(1, "usb:h:2")], settled=True)
    first = table.slots[0]
    assert (first.state, first.local_id, first.key, first.name) == (MISSING, None, "usb:h:1", "GameSir")
    assert table.slots[1].local_id == 1


def test_a_returning_controller_reclaims_its_own_slot() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"), pad(2, "usb:h:3"))
    table.update([pad(2, "usb:h:3")], settled=True)
    # Back with new instance ids, in the reverse order.
    table.update([pad(2, "usb:h:3"), pad(9, "usb:h:2"), pad(8, "usb:h:1")], settled=True)
    assert [s.local_id for s in table.slots] == [8, 9, 2]


def test_a_new_controller_takes_the_lowest_missing_slot_of_its_kind() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"), pad(2, "usb:h:3"))
    table.update([pad(2, "usb:h:3")], settled=True)
    table.update([pad(2, "usb:h:3"), pad(5, "usb:h:9")], settled=True)
    assert layout(table) == [("gamepad", 5, CONNECTED), ("gamepad", None, MISSING), ("gamepad", 2, CONNECTED)]


def test_a_new_controller_does_not_take_another_kinds_slot() -> None:
    table = frozen(puck(0, "usb:h:1"), pad(0, "usb:h:2"))
    table.update([pad(0, "usb:h:2")], settled=True)
    table.update([pad(0, "usb:h:2"), pad(4, "usb:h:9")], settled=True)
    assert layout(table) == [("mouse3d", None, MISSING), ("gamepad", 0, CONNECTED), ("gamepad", 4, CONNECTED)]


def test_on_a_single_slot_station_any_kind_takes_over() -> None:
    table = frozen(pad(0, "usb:h:1"))
    table.update([], settled=True)
    assert layout(table) == [("gamepad", None, MISSING)]
    table.update([puck(3, "usb:h:2")], settled=True)
    assert layout(table) == [("mouse3d", 3, CONNECTED)]


def test_a_controller_with_nowhere_to_go_appends() -> None:
    table = frozen(pad(0, "usb:h:1"))
    table.update([pad(0, "usb:h:1"), puck(0, "usb:h:2")], settled=True)
    assert layout(table) == [("gamepad", 0, CONNECTED), ("mouse3d", 0, CONNECTED)]


def test_an_arrival_never_moves_a_connected_slot() -> None:
    table = frozen(pad(0, "usb:h:5"))
    table.update([pad(0, "usb:h:5"), pad(1, "usb:h:1")], settled=True)
    assert [s.local_id for s in table.slots] == [0, 1]


def test_a_puck_reconnecting_under_the_same_id_keeps_its_slot() -> None:
    table = frozen(puck(0, "usb:h:1"), pad(0, "usb:h:2"))
    table.update([pad(0, "usb:h:2")], settled=True)
    table.update([puck(0, "usb:h:1"), pad(0, "usb:h:2")], settled=True)
    assert layout(table) == [("mouse3d", 0, CONNECTED), ("gamepad", 0, CONNECTED)]


def test_an_unchanged_frame_reports_no_change() -> None:
    table = frozen(pad(0, "usb:h:1"))
    assert not table.update([pad(0, "usb:h:1")], settled=True)


# -- forget ------------------------------------------------------------------


def test_forget_turns_a_missing_slot_reserved_and_keeps_its_place() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    table.update([pad(1, "usb:h:2")], settled=True)
    assert table.forget(0)
    assert layout(table) == [("gamepad", None, RESERVED), ("gamepad", 1, CONNECTED)]


@pytest.mark.parametrize("index", [-1, 1, 5])
def test_forget_refuses_anything_but_a_missing_slot(index: int) -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    table.update([pad(1, "usb:h:2")], settled=True)
    before = table.slots
    assert not table.forget(index)
    assert table.slots == before


def test_a_reserved_slot_is_refilled_by_the_next_of_its_kind() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    table.update([pad(1, "usb:h:2")], settled=True)
    table.forget(0)
    table.update([pad(1, "usb:h:2"), pad(6, "usb:h:7")], settled=True)
    assert layout(table)[0] == ("gamepad", 6, CONNECTED)


def test_a_reserved_slot_is_reclaimed_by_its_own_socket_first() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"), pad(2, "usb:h:3"))
    table.update([pad(2, "usb:h:3")], settled=True)
    table.forget(0)
    table.update([pad(2, "usb:h:3"), pad(7, "usb:h:2")], settled=True)
    assert [s.state for s in table.slots] == [RESERVED, CONNECTED, CONNECTED]


# -- rebuild -----------------------------------------------------------------


def test_rebuild_starts_over_from_port_order() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    table.update([pad(1, "usb:h:2")], settled=True)
    table.rebuild()
    assert table.seeding
    table.update([pad(1, "usb:h:2")], settled=True)
    assert layout(table) == [("gamepad", 1, CONNECTED)]


# -- property ----------------------------------------------------------------

_DEVICES = [pad(i, f"usb:h:{i + 1}") for i in range(4)] + [puck(0, "usb:h:9"), pad(10, None)]


@given(frames=st.lists(st.sets(st.sampled_from(range(len(_DEVICES)))), min_size=1, max_size=12))
def test_a_controller_that_stays_attached_never_changes_slot(frames: list[set[int]]) -> None:
    table = frozen(*_DEVICES)
    previous = {(s.kind, s.local_id): i for i, s in enumerate(table.slots) if s.state == CONNECTED}
    for frame in frames:
        live = [_DEVICES[i] for i in sorted(frame)]
        table.update(live, settled=True)
        now = {(s.kind, s.local_id): i for i, s in enumerate(table.slots) if s.state == CONNECTED}
        for ident, index in now.items():
            if ident in previous:
                assert previous[ident] == index
        assert len(now) == len(live)
        previous = now
