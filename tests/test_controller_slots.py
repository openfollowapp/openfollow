# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Session slot assignment: port order at startup, missing slots after."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from openfollow.input.controller_slots import (
    CONNECTED,
    EMPTY,
    MISSING,
    NO_MARKER,
    NOTE_BUTTON_MAP_OTHER_MODEL,
    NOTE_CANNOT_IDENTIFY,
    RESERVED,
    ControllerSlotTable,
    LiveController,
    SlotPin,
    pick_marker,
    slot_markers,
    slot_ref,
)

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def pad(local_id: int, key: str | None, name: str = "Pad", notes: tuple[str, ...] = ()) -> LiveController:
    return LiveController(kind="gamepad", local_id=local_id, key=key, name=name, notes=notes)


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


def test_a_returning_controller_reclaims_before_a_newcomer_arriving_with_it() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    table.update([pad(1, "usb:h:2")], settled=True)
    # The newcomer's port sorts first, so it is looked at before the owner.
    table.update([pad(1, "usb:h:2"), pad(5, "usb:h:0"), pad(6, "usb:h:1")], settled=True)
    assert [s.local_id for s in table.slots] == [6, 1, 5]


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


# -- notes -------------------------------------------------------------------


def test_a_slot_carries_its_controllers_notes_from_the_first_frame() -> None:
    table = frozen(pad(0, "usb:h:1", notes=(NOTE_CANNOT_IDENTIFY,)), pad(1, "usb:h:2"))
    assert [s.notes for s in table.slots] == [(NOTE_CANNOT_IDENTIFY,), ()]


def test_a_connected_slots_notes_follow_its_controller_without_counting_as_a_change() -> None:
    table = frozen(pad(0, "usb:h:1"))
    assert not table.update([pad(0, "usb:h:1", notes=(NOTE_BUTTON_MAP_OTHER_MODEL,))], settled=True)
    assert table.slots[0].notes == (NOTE_BUTTON_MAP_OTHER_MODEL,)


def test_a_missing_slot_keeps_the_notes_of_the_controller_it_last_held() -> None:
    table = frozen(pad(0, "usb:h:1", notes=(NOTE_CANNOT_IDENTIFY,)), pad(1, "usb:h:2"))
    table.update([pad(1, "usb:h:2")], settled=True)
    assert (table.slots[0].state, table.slots[0].notes) == (MISSING, (NOTE_CANNOT_IDENTIFY,))


def test_forgetting_a_slot_clears_its_notes() -> None:
    table = frozen(pad(0, "usb:h:1", notes=(NOTE_CANNOT_IDENTIFY,)), pad(1, "usb:h:2"))
    table.update([pad(1, "usb:h:2")], settled=True)
    assert table.forget(0)
    assert (table.slots[0].state, table.slots[0].notes) == (RESERVED, ())
    # A frame after the forget leaves them cleared.
    table.update([pad(1, "usb:h:2")], settled=True)
    assert table.slots[0].notes == ()


def test_a_controller_taking_over_a_missing_slot_brings_its_own_notes() -> None:
    table = frozen(pad(0, "usb:h:1", notes=(NOTE_CANNOT_IDENTIFY,)), pad(1, "usb:h:2"))
    table.update([pad(1, "usb:h:2")], settled=True)
    table.update([pad(1, "usb:h:2"), pad(5, "usb:h:9")], settled=True)
    assert (table.slots[0].local_id, table.slots[0].notes) == (5, ())


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


def test_a_slot_ref_changes_with_whatever_the_row_shows() -> None:
    table = frozen(pad(0, "usb:h:1", "GameSir"), pad(1, "usb:h:2", "GameSir"))
    connected, other = (slot_ref(s) for s in table.slots)
    assert connected != other
    table.update([pad(1, "usb:h:2", "GameSir")], settled=True)
    missing = slot_ref(table.slots[0])
    assert missing != connected
    table.forget(0)
    assert slot_ref(table.slots[0]) not in (connected, missing)
    table.update([pad(1, "usb:h:2", "GameSir"), pad(7, "usb:h:9", "GameSir")], settled=True)
    assert slot_ref(table.slots[0]) not in (connected, missing)


# -- pins --------------------------------------------------------------------


def pinned_table(pins: dict[int, SlotPin], *live: LiveController) -> ControllerSlotTable:
    table = ControllerSlotTable(clock=_Clock(), pins=pins)
    table.update(live, settled=True)
    return table


def pinned_layout(table: ControllerSlotTable) -> list[tuple[str, int | None, str, bool]]:
    return [(s.kind, s.local_id, s.state, s.pinned) for s in table.slots]


_SOCKET_9 = SlotPin(key="usb:h:9", kind="gamepad", label="Xbox Pad")


def test_a_pinned_socket_takes_its_slot_whatever_the_port_order() -> None:
    table = pinned_table({0: _SOCKET_9}, pad(0, "usb:h:1"), pad(1, "usb:h:9"))
    assert pinned_layout(table) == [("gamepad", 1, CONNECTED, True), ("gamepad", 0, CONNECTED, False)]


def test_without_pins_the_order_is_the_port_order() -> None:
    table = pinned_table({}, pad(0, "usb:h:9"), pad(1, "usb:h:1"))
    assert pinned_layout(table) == [("gamepad", 1, CONNECTED, False), ("gamepad", 0, CONNECTED, False)]


def test_a_pin_whose_socket_is_empty_shows_missing_without_shifting_the_others() -> None:
    table = pinned_table({0: _SOCKET_9}, pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    assert pinned_layout(table) == [
        ("gamepad", None, MISSING, True),
        ("gamepad", 0, CONNECTED, False),
        ("gamepad", 1, CONNECTED, False),
    ]
    assert table.slots[0].name == "Xbox Pad"
    assert table.slots[0].key == "usb:h:9"


def test_a_pin_above_the_controllers_leaves_empty_slots_below_it() -> None:
    table = pinned_table({2: _SOCKET_9}, pad(0, "usb:h:9"))
    assert pinned_layout(table) == [
        ("", None, EMPTY, False),
        ("", None, EMPTY, False),
        ("gamepad", 0, CONNECTED, True),
    ]


def test_other_hardware_in_a_pinned_socket_takes_the_slot() -> None:
    table = pinned_table({0: _SOCKET_9}, pad(0, "usb:h:1"), puck(4, "usb:h:9"))
    assert pinned_layout(table)[0] == ("mouse3d", 4, CONNECTED, True)


def test_a_controller_back_in_its_pinned_socket_reclaims_the_slot() -> None:
    table = pinned_table({1: _SOCKET_9}, pad(0, "usb:h:1"), pad(1, "usb:h:9"))
    table.update([pad(0, "usb:h:1")], settled=True)
    assert pinned_layout(table)[1] == ("gamepad", None, MISSING, True)
    table.update([pad(0, "usb:h:1"), puck(7, "usb:h:9")], settled=True)
    assert pinned_layout(table)[1] == ("mouse3d", 7, CONNECTED, True)


def test_a_pinned_slot_never_goes_to_another_socket() -> None:
    table = pinned_table({0: _SOCKET_9})
    table.update([pad(3, "usb:h:2")], settled=True)
    assert pinned_layout(table) == [("gamepad", None, MISSING, True), ("gamepad", 3, CONNECTED, False)]


def test_an_empty_slot_takes_a_controller_of_any_kind() -> None:
    table = pinned_table({1: _SOCKET_9}, pad(0, "usb:h:9"))
    table.update([pad(0, "usb:h:9"), puck(2, "usb:h:3")], settled=True)
    assert pinned_layout(table) == [("mouse3d", 2, CONNECTED, False), ("gamepad", 0, CONNECTED, True)]


def test_pinning_moves_the_controller_and_swaps_with_the_one_there() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    assert table.pin(0, "gamepad", 1)
    assert pinned_layout(table) == [("gamepad", 1, CONNECTED, True), ("gamepad", 0, CONNECTED, False)]
    assert table.pins == {0: SlotPin(key="usb:h:2", kind="gamepad", label="Pad")}


def test_the_displaced_controller_takes_its_pin_along() -> None:
    table = pinned_table({0: SlotPin("usb:h:1", "gamepad", "Pad")}, pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    assert table.pin(0, "gamepad", 1)
    assert pinned_layout(table) == [("gamepad", 1, CONNECTED, True), ("gamepad", 0, CONNECTED, True)]
    assert {i: pin.key for i, pin in table.pins.items()} == {0: "usb:h:2", 1: "usb:h:1"}


def test_a_pinned_controller_pinned_elsewhere_leaves_its_old_pin() -> None:
    table = pinned_table({1: SlotPin("usb:h:2", "gamepad", "Pad")}, pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    assert table.pin(0, "gamepad", 1)
    assert {i: pin.key for i, pin in table.pins.items()} == {0: "usb:h:2"}
    assert pinned_layout(table) == [("gamepad", 1, CONNECTED, True), ("gamepad", 0, CONNECTED, False)]


def test_pinning_in_place_moves_nothing() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    assert table.pin(1, "gamepad", 1)
    assert pinned_layout(table) == [("gamepad", 0, CONNECTED, False), ("gamepad", 1, CONNECTED, True)]


def test_pinning_past_the_last_slot_leaves_empty_slots() -> None:
    table = frozen(pad(0, "usb:h:1"))
    assert table.pin(2, "gamepad", 0)
    assert pinned_layout(table) == [("", None, EMPTY, False), ("", None, EMPTY, False), ("gamepad", 0, CONNECTED, True)]


def test_moving_a_pin_down_drops_the_empty_slots_it_no_longer_needs() -> None:
    table = pinned_table({2: _SOCKET_9}, pad(0, "usb:h:9"))
    assert table.pin(0, "gamepad", 0)
    assert pinned_layout(table) == [("gamepad", 0, CONNECTED, True)]


@pytest.mark.parametrize(("kind", "local_id"), [("gamepad", 0), ("mouse3d", 1)])
def test_a_pad_and_a_puck_pin_in_either_order(kind: str, local_id: int) -> None:
    table = frozen(pad(0, "usb:h:1"), puck(1, "usb:h:2"))
    assert table.pin(1, kind, local_id) and table.pin(0, kind, local_id)
    assert (table.slots[0].kind, table.slots[0].local_id) == (kind, local_id)


@pytest.mark.parametrize(
    ("controllers", "kind", "local_id"),
    [((pad(0, None),), "gamepad", 0), ((pad(0, "usb:h:1"),), "gamepad", 9), ((pad(0, "usb:h:1"),), "mouse3d", 0)],
    ids=["no stable port", "not in a slot", "another kind"],
)
def test_a_controller_without_a_socket_or_a_slot_cannot_be_pinned(
    controllers: tuple[LiveController, ...], kind: str, local_id: int
) -> None:
    table = frozen(*controllers)
    before = table.slots
    assert table.pin(0, kind, local_id) is False
    assert table.slots == before
    assert table.pins == {}


def test_a_negative_slot_cannot_be_pinned() -> None:
    table = frozen(pad(0, "usb:h:1"))
    assert table.pin(-1, "gamepad", 0) is False


def test_unpinning_moves_nothing() -> None:
    table = pinned_table({0: _SOCKET_9}, pad(0, "usb:h:1"), pad(1, "usb:h:9"))
    assert table.unpin(0)
    assert pinned_layout(table) == [("gamepad", 1, CONNECTED, False), ("gamepad", 0, CONNECTED, False)]
    assert table.pins == {}
    assert table.unpin(0) is False


def test_an_unpinned_missing_slot_is_an_ordinary_missing_slot() -> None:
    table = pinned_table({0: _SOCKET_9}, pad(0, "usb:h:1"))
    table.unpin(0)
    table.update([pad(0, "usb:h:1"), pad(5, "usb:h:4")], settled=True)
    assert pinned_layout(table)[0] == ("gamepad", 5, CONNECTED, False)


def test_forgetting_a_pinned_missing_slot_keeps_its_pin() -> None:
    table = pinned_table({0: _SOCKET_9}, pad(0, "usb:h:1"))
    assert table.forget(0)
    assert pinned_layout(table)[0] == ("gamepad", None, RESERVED, True)
    assert table.pins == {0: _SOCKET_9}


def test_new_pins_from_the_configuration_move_their_controllers() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    table.set_pins({0: SlotPin("usb:h:2", "gamepad", "Pad")})
    assert pinned_layout(table) == [("gamepad", 1, CONNECTED, True), ("gamepad", 0, CONNECTED, False)]


def test_a_new_pin_for_an_empty_socket_moves_the_slot_holder_to_the_end() -> None:
    table = frozen(pad(0, "usb:h:1"), pad(1, "usb:h:2"))
    table.set_pins({0: _SOCKET_9})
    assert pinned_layout(table) == [
        ("gamepad", None, MISSING, True),
        ("gamepad", 1, CONNECTED, False),
        ("gamepad", 0, CONNECTED, False),
    ]


def test_a_socket_repinned_to_another_slot_leaves_the_old_one() -> None:
    table = pinned_table({0: _SOCKET_9}, pad(0, "usb:h:1"))
    table.set_pins({2: _SOCKET_9})
    assert pinned_layout(table) == [
        ("gamepad", None, MISSING, False),
        ("gamepad", 0, CONNECTED, False),
        ("gamepad", None, MISSING, True),
    ]
    assert table.pins == {2: _SOCKET_9}


def test_removing_every_pin_from_the_configuration_moves_nothing() -> None:
    table = pinned_table({0: _SOCKET_9}, pad(0, "usb:h:1"), pad(1, "usb:h:9"))
    table.set_pins({})
    assert pinned_layout(table) == [("gamepad", 1, CONNECTED, False), ("gamepad", 0, CONNECTED, False)]


def test_unchanged_pins_from_the_configuration_change_nothing() -> None:
    table = pinned_table({0: _SOCKET_9}, pad(0, "usb:h:1"), pad(1, "usb:h:9"))
    before = table.slots
    table.set_pins({0: _SOCKET_9})
    assert table.slots == before


def test_pins_set_while_seeding_seed_the_next_frame() -> None:
    table = ControllerSlotTable(clock=_Clock())
    table.update([pad(0, "usb:h:1"), pad(1, "usb:h:9")], settled=False)
    table.set_pins({0: _SOCKET_9})
    table.update([pad(0, "usb:h:1"), pad(1, "usb:h:9")], settled=True)
    assert pinned_layout(table) == [("gamepad", 1, CONNECTED, True), ("gamepad", 0, CONNECTED, False)]


def test_a_slot_ref_changes_when_the_slot_is_pinned() -> None:
    table = frozen(pad(0, "usb:h:1"))
    before = slot_ref(table.slots[0])
    table.pin(0, "gamepad", 0)
    assert slot_ref(table.slots[0]) != before


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


@given(frames=st.lists(st.sets(st.sampled_from(range(len(_DEVICES)))), min_size=1, max_size=12))
def test_with_pins_a_controller_that_stays_attached_never_changes_slot(frames: list[set[int]]) -> None:
    pins = {0: SlotPin("usb:h:4", "gamepad", "Pad"), 3: SlotPin("usb:h:9", "mouse3d", "3D Mouse")}
    table = ControllerSlotTable(clock=_Clock(), pins=pins)
    table.update(_DEVICES, settled=True)
    previous = {(s.kind, s.local_id): i for i, s in enumerate(table.slots) if s.state == CONNECTED}
    for frame in frames:
        live = [_DEVICES[i] for i in sorted(frame)]
        table.update(live, settled=True)
        now = {(s.kind, s.local_id): i for i, s in enumerate(table.slots) if s.state == CONNECTED}
        for ident, index in now.items():
            if ident in previous:
                assert previous[ident] == index
        assert len(now) == len(live)
        for index, pin in pins.items():
            assert table.slots[index].key == pin.key
        previous = now


def test_a_pin_dropped_before_the_first_seed_moves_nothing() -> None:
    table = ControllerSlotTable(clock=lambda: 0.0)
    table.set_pins({2: SlotPin(key="usb:h:1", kind="gamepad", label="Pad")})
    assert table.unpin(2) is True
    assert table.pins == {}
    assert table.slots == ()


# --- the marker each slot drives -------------------------------------------


def test_without_picks_each_slot_drives_its_place_in_the_list() -> None:
    assert slot_markers(4, [10, 11, 12], {}) == [10, 11, 12, None]


def test_slots_without_a_pick_take_the_markers_no_slot_picked() -> None:
    assert slot_markers(3, [10, 11, 12], {0: 12}) == [12, 10, 11]


def test_a_slot_can_be_left_without_a_marker() -> None:
    assert slot_markers(3, [10, 11, 12], {0: NO_MARKER}) == [None, 10, 11]


def test_a_pick_for_a_marker_that_is_not_controlled_counts_as_none() -> None:
    assert slot_markers(2, [10, 11], {0: 99}) == [10, 11]


def test_a_pick_for_a_slot_not_there_yet_keeps_its_marker_aside() -> None:
    assert slot_markers(2, [10, 11, 12], {3: 10}) == [11, 12]


def test_picking_another_slots_marker_swaps_the_two() -> None:
    picks = pick_marker(3, [10, 11, 12], {}, 0, 12)
    assert picks == {0: 12, 1: 11, 2: 10}
    assert slot_markers(3, [10, 11, 12], picks) == [12, 11, 10]


def test_picking_for_a_slot_without_a_marker_leaves_the_other_slot_without_one() -> None:
    picks = pick_marker(3, [10, 11], {}, 2, 10)
    assert slot_markers(3, [10, 11], picks) == [None, 11, 10]


def test_picking_an_unassigned_marker_moves_no_other_slot() -> None:
    picks = pick_marker(2, [10, 11, 12], {}, 0, 12)
    assert slot_markers(2, [10, 11, 12], picks) == [12, 11]
    # The marker set free goes to the next slot that has none, not to one that has.
    assert slot_markers(3, [10, 11, 12], picks) == [12, 11, 10]


def test_picking_a_slots_own_marker_changes_nothing() -> None:
    picks = pick_marker(2, [10, 11], {}, 1, 11)
    assert slot_markers(2, [10, 11], picks) == [10, 11]


def test_a_marker_picked_for_a_slot_not_there_yet_comes_back_to_the_picker() -> None:
    picks = pick_marker(2, [10, 11, 12], {3: 12}, 0, 12)
    assert picks is not None
    assert slot_markers(4, [10, 11, 12], picks) == [12, 11, None, 10]


def test_a_slot_left_without_a_marker_stays_without_one() -> None:
    assert slot_markers(3, [10, 11, 12], {1: NO_MARKER}) == [10, None, 11]
    picks = pick_marker(3, [10, 11, 12], {1: NO_MARKER}, 0, 12)
    assert slot_markers(3, [10, 11, 12], picks) == [12, None, 11]


@pytest.mark.parametrize(("index", "marker"), [(0, 99), (-1, 10), (2, 10)])
def test_a_pick_needs_a_slot_and_a_controlled_marker(index: int, marker: int) -> None:
    assert pick_marker(2, [10, 11], {}, index, marker) is None


@given(
    st.integers(1, 5),
    st.lists(st.integers(1, 8), unique=True, max_size=6),
    st.dictionaries(st.integers(0, 6), st.integers(0, 8), max_size=6),
    st.data(),
)
def test_a_pick_moves_at_most_two_slots_and_never_doubles_a_marker(slot_count, controlled, raw, data) -> None:
    seen: set[int] = set()
    picks = {}
    for index, marker in raw.items():
        if marker and marker in seen:
            continue
        seen.add(marker)
        picks[index] = marker
    if not controlled:
        return
    index = data.draw(st.integers(0, slot_count - 1))
    marker = data.draw(st.sampled_from(controlled))
    before = slot_markers(slot_count, controlled, picks)
    new = pick_marker(slot_count, controlled, picks, index, marker)
    assert new is not None
    after = slot_markers(slot_count, controlled, new)
    assert after[index] == marker
    held = [m for m in after if m is not None]
    assert len(held) == len(set(held))
    changed = [i for i in range(slot_count) if before[i] != after[i]]
    assert len(changed) <= 2
