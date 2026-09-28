# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Which slot each controller occupies, held for the whole session.

Slots are seeded in port order once the controllers attached at startup have
been found, then frozen: a controller that leaves keeps its slot as *missing*
instead of closing the gap, so nobody behind it changes marker. Pure logic; the
main loop is the only caller that mutates it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from openfollow.input.controller_identity import natural_sort_key

CONNECTED = "connected"
MISSING = "missing"
RESERVED = "reserved"

# What a slot's controller can't do. Published through /api/stats, so the
# values are a wire interface.
NOTE_BUTTONS_UNRECOGNISED = "buttons_unrecognised"
NOTE_BUTTON_MAP_OTHER_MODEL = "button_map_other_model"
NOTE_CANNOT_IDENTIFY = "cannot_identify"

# The first 3D-mouse scan normally settles well inside this; it bounds a puck
# that never answers so gamepads are not left unfrozen.
SEED_DEADLINE_S = 3.0

# Keyless controllers keep today's order: 3D mice first, then gamepads.
_KIND_ORDER = {"mouse3d": 0, "gamepad": 1}


@dataclass(frozen=True)
class LiveController:
    """A controller that is attached now. ``local_id`` is its handler's id."""

    kind: str
    local_id: int
    key: str | None
    name: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlotEntry:
    """One slot. ``local_id`` is set only while the slot is connected.

    ``notes`` follow the controller while it is connected, stay with the slot
    once it goes missing and are cleared when it is forgotten. They never count
    as a change of the slot table.
    """

    kind: str
    key: str | None
    name: str
    state: str
    local_id: int | None = None
    notes: tuple[str, ...] = field(default=(), compare=False)


def _port_order(controllers: Sequence[LiveController]) -> list[LiveController]:
    keyed = sorted((c for c in controllers if c.key is not None), key=lambda c: natural_sort_key(c.key or ""))
    keyless = sorted((c for c in controllers if c.key is None), key=lambda c: (_KIND_ORDER.get(c.kind, 2), c.local_id))
    return keyed + keyless


def _unique_keys(controllers: Sequence[LiveController]) -> list[LiveController]:
    """Controllers behind one USB device share its key; only the first keeps it."""
    seen: set[str] = set()
    out: list[LiveController] = []
    for c in sorted(controllers, key=lambda c: (_KIND_ORDER.get(c.kind, 2), c.local_id)):
        if c.key is not None and c.key in seen:
            c = replace(c, key=None)
        elif c.key is not None:
            seen.add(c.key)
        out.append(c)
    return out


def _connected(c: LiveController) -> SlotEntry:
    return SlotEntry(kind=c.kind, key=c.key, name=c.name, state=CONNECTED, local_id=c.local_id, notes=c.notes)


def slot_ref(slot: SlotEntry) -> str:
    """What a slot row was showing, so an action that arrives late can tell the slot changed."""
    return f"{slot.kind}|{slot.key or ''}|{slot.name}|{slot.state}"


class ControllerSlotTable:
    """Session slot assignment. Read :attr:`slots` from any thread; mutate from one."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, seed_deadline_s: float = SEED_DEADLINE_S):
        self._clock = clock
        self._seed_deadline_s = seed_deadline_s
        self._seed_started = clock()
        self._seeding = True
        self._slots: tuple[SlotEntry, ...] = ()

    @property
    def slots(self) -> tuple[SlotEntry, ...]:
        """Immutable snapshot, swapped whole, so readers never see a half update."""
        return self._slots

    @property
    def seeding(self) -> bool:
        return self._seeding

    def update(self, live: Sequence[LiveController], *, settled: bool) -> bool:
        """Fold this frame's attached controllers in; True when the slots changed.

        ``settled`` is True once every controller kind has finished its first
        scan; seeding ends then, or at the deadline.
        """
        controllers = _unique_keys(live)
        if self._seeding:
            slots = tuple(_connected(c) for c in _port_order(controllers))
            if settled or self._clock() - self._seed_started >= self._seed_deadline_s:
                self._seeding = False
        else:
            slots = self._frozen_update(controllers)
        changed = slots != self._slots
        self._slots = slots
        return changed

    def _frozen_update(self, controllers: Sequence[LiveController]) -> tuple[SlotEntry, ...]:
        by_id: dict[tuple[str, int | None], LiveController] = {(c.kind, c.local_id): c for c in controllers}

        def carried(s: SlotEntry) -> SlotEntry:
            if s.state != CONNECTED:
                return s
            live = by_id.get((s.kind, s.local_id))
            return replace(s, state=MISSING, local_id=None) if live is None else replace(s, notes=live.notes)

        slots = [carried(s) for s in self._slots]
        held = {(s.kind, s.local_id) for s in slots if s.state == CONNECTED}
        newcomers = _port_order([c for c in controllers if (c.kind, c.local_id) not in held])
        # Every returning controller reclaims its own slot before any newcomer
        # falls back to a vacant one, or one arriving in the same frame (a hub
        # re-enumerating) could take a slot its owner was about to reclaim.
        waiting = []
        for c in newcomers:
            index = next(
                (
                    i
                    for i, s in enumerate(slots)
                    if c.key is not None and s.state != CONNECTED and s.kind == c.kind and s.key == c.key
                ),
                None,
            )
            if index is None:
                waiting.append(c)
            else:
                slots[index] = _connected(c)
        for c in waiting:
            index = self._vacant_for(slots, c)
            if index is None:
                slots.append(_connected(c))
            else:
                slots[index] = _connected(c)
        return tuple(slots)

    @staticmethod
    def _vacant_for(slots: Sequence[SlotEntry], c: LiveController) -> int | None:
        vacant = [i for i, s in enumerate(slots) if s.state != CONNECTED]
        if len(slots) == 1 and vacant:
            return 0
        return next((i for i in vacant if slots[i].kind == c.kind), None)

    def forget(self, index: int) -> bool:
        """Silence a missing slot: it keeps its position but drives no marker and raises no notes."""
        if not 0 <= index < len(self._slots) or self._slots[index].state != MISSING:
            return False
        slots = list(self._slots)
        slots[index] = replace(slots[index], state=RESERVED, notes=())
        self._slots = tuple(slots)
        return True
