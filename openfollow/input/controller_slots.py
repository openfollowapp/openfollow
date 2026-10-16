# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Which slot each controller occupies, held for the whole session.

Slots are seeded in port order once the controllers attached at startup have
been found, then frozen: a controller that leaves keeps its slot as *missing*
instead of closing the gap, so nobody behind it changes marker. A slot pinned to
a USB socket belongs to whatever is plugged into that socket. Pure logic; the
main loop is the only caller that mutates it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from openfollow.input.controller_identity import natural_sort_key

CONNECTED = "connected"
MISSING = "missing"
RESERVED = "reserved"
# A slot below a pin that nothing fills: silent, drives no marker, any kind takes it.
EMPTY = "empty"

# A slot's pick that leaves it without a marker.
NO_MARKER = 0

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
    as a change of the slot table. A ``pinned`` slot's ``key`` is its socket.
    """

    kind: str
    key: str | None
    name: str
    state: str
    local_id: int | None = None
    notes: tuple[str, ...] = field(default=(), compare=False)
    pinned: bool = False


@dataclass(frozen=True)
class SlotPin:
    """A socket pinned to a slot, with what was pinned there, shown while it is empty."""

    key: str
    kind: str
    label: str


_EMPTY = SlotEntry(kind="", key=None, name="", state=EMPTY)


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


def _connected(c: LiveController, *, pinned: bool = False) -> SlotEntry:
    return SlotEntry(
        kind=c.kind, key=c.key, name=c.name, state=CONNECTED, local_id=c.local_id, notes=c.notes, pinned=pinned
    )


def _pinned_absent(pin: SlotPin) -> SlotEntry:
    return SlotEntry(kind=pin.kind, key=pin.key, name=pin.label, state=MISSING, pinned=True)


def _trimmed(slots: list[SlotEntry]) -> tuple[SlotEntry, ...]:
    """Without trailing empty slots: an empty slot exists only to keep a pin's place."""
    while slots and slots[-1].state == EMPTY:
        slots.pop()
    return tuple(slots)


def _valid_picks(picks: Mapping[int, int], controlled: Sequence[int]) -> dict[int, int]:
    """The picks that still name a controlled marker, or ``NO_MARKER``."""
    allowed = set(controlled)
    return {index: marker for index, marker in picks.items() if marker == NO_MARKER or marker in allowed}


def slot_markers(slot_count: int, controlled: Sequence[int], picks: Mapping[int, int]) -> list[int | None]:
    """Each slot's marker: its pick, else the next controlled marker no slot picked, in list order.

    ``picks`` maps a 0-based slot to a marker, ``NO_MARKER`` for a slot left without one. A pick
    naming a marker that is not controlled counts as no pick. Without picks this is
    ``controlled[slot]``.
    """
    valid = _valid_picks(picks, controlled)
    picked = set(valid.values())
    spare = iter([marker for marker in controlled if marker not in picked])
    return [(valid[index] or None) if index in valid else next(spare, None) for index in range(slot_count)]


def pick_marker(
    slot_count: int, controlled: Sequence[int], picks: Mapping[int, int], index: int, marker: int
) -> dict[int, int] | None:
    """The picks after giving slot ``index`` the controlled ``marker``; ``None`` when it can't have it.

    Every slot that drives a marker keeps it as a pick, so only the slot that held ``marker``
    changes: it takes ``index``'s previous marker, or none.
    """
    if marker not in controlled or not 0 <= index < slot_count:
        return None
    current = slot_markers(slot_count, controlled, picks)
    valid = _valid_picks(picks, controlled)
    new = {i: m for i, m in valid.items() if i >= slot_count or m == NO_MARKER}
    new.update({i: m for i, m in enumerate(current) if m is not None})
    holder = next((i for i, m in new.items() if m == marker and i != index), None)
    if holder is not None:
        new[holder] = current[index] or NO_MARKER
    new[index] = marker
    return new


def slot_ref(slot: SlotEntry) -> str:
    """What a slot row was showing, so an action that arrives late can tell the slot changed."""
    return f"{slot.kind}|{slot.key or ''}|{slot.name}|{slot.state}|{'pinned' if slot.pinned else ''}"


class ControllerSlotTable:
    """Session slot assignment. Read :attr:`slots` from any thread; mutate from one."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        seed_deadline_s: float = SEED_DEADLINE_S,
        pins: Mapping[int, SlotPin] | None = None,
    ):
        self._clock = clock
        self._seed_deadline_s = seed_deadline_s
        self._seed_started = clock()
        self._seeding = True
        self._slots: tuple[SlotEntry, ...] = ()
        # 0-based slot index -> the socket pinned there.
        self._pins: dict[int, SlotPin] = dict(pins or {})

    @property
    def slots(self) -> tuple[SlotEntry, ...]:
        """Immutable snapshot, swapped whole, so readers never see a half update."""
        return self._slots

    @property
    def seeding(self) -> bool:
        return self._seeding

    @property
    def pins(self) -> dict[int, SlotPin]:
        """0-based slot index -> the socket pinned there; a copy."""
        return dict(self._pins)

    def update(self, live: Sequence[LiveController], *, settled: bool) -> bool:
        """Fold this frame's attached controllers in; True when the slots changed.

        ``settled`` is True once every controller kind has finished its first
        scan; seeding ends then, or at the deadline.
        """
        controllers = _unique_keys(live)
        if self._seeding:
            slots = self._seeded(controllers)
            if settled or self._clock() - self._seed_started >= self._seed_deadline_s:
                self._seeding = False
        else:
            slots = self._frozen_update(controllers)
        changed = slots != self._slots
        self._slots = slots
        return changed

    def _seeded(self, controllers: Sequence[LiveController]) -> tuple[SlotEntry, ...]:
        """Pinned sockets at their pins, everything else in port order around them."""
        by_key = {c.key: c for c in controllers if c.key is not None}
        placed: dict[int, SlotEntry] = {}
        for index, pin in self._pins.items():
            live = by_key.get(pin.key)
            placed[index] = _pinned_absent(pin) if live is None else _connected(live, pinned=True)
        pinned_keys = {pin.key for pin in self._pins.values()}
        free = (i for i in range(len(controllers) + len(placed)) if i not in placed)
        for c in _port_order([c for c in controllers if c.key not in pinned_keys]):
            placed[next(free)] = _connected(c)
        return _trimmed([placed.get(i, _EMPTY) for i in range(max(placed, default=-1) + 1)])

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
        pinned_at = {pin.key: index for index, pin in self._pins.items()}
        # Every returning controller reclaims its own slot before any newcomer
        # falls back to a vacant one, or one arriving in the same frame (a hub
        # re-enumerating) could take a slot its owner was about to reclaim. A
        # pinned socket's slot is its own, whatever is plugged in.
        waiting = []
        for c in newcomers:
            index = pinned_at.get(c.key) if c.key is not None else None
            if index is not None and slots[index].state != CONNECTED:
                slots[index] = _connected(c, pinned=True)
                continue
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

    def _vacant_for(self, slots: Sequence[SlotEntry], c: LiveController) -> int | None:
        vacant = [i for i, s in enumerate(slots) if s.state != CONNECTED and i not in self._pins]
        if len(slots) == 1 and vacant:
            return 0
        return next((i for i in vacant if slots[i].kind == c.kind or slots[i].state == EMPTY), None)

    def pin(self, index: int, kind: str, local_id: int) -> bool:
        """Pin the socket of the connected controller ``(kind, local_id)`` to slot ``index``.

        The controller moves there. Whatever held that slot takes the
        controller's old one, its pin along with it. False when the controller
        holds no slot or has no socket to pin.
        """
        held = next(
            (i for i, s in enumerate(self._slots) if s.state == CONNECTED and (s.kind, s.local_id) == (kind, local_id)),
            None,
        )
        if held is None or index < 0:
            return False
        moving = self._slots[held]
        key = moving.key
        if key is None:
            return False
        slots = list(self._slots) + [_EMPTY] * (index + 1 - len(self._slots))
        own_pin = next((i for i, pin in self._pins.items() if pin.key == key), None)
        if own_pin is not None:
            del self._pins[own_pin]
        displaced, displaced_pin = slots[index], self._pins.pop(index, None)
        slots[held] = replace(displaced, pinned=displaced_pin is not None)
        if displaced_pin is not None:
            self._pins[held] = displaced_pin
        slots[index] = replace(moving, pinned=True)
        self._pins[index] = SlotPin(key=key, kind=moving.kind, label=moving.name)
        self._slots = _trimmed(slots)
        return True

    def unpin(self, index: int) -> bool:
        """Drop slot ``index``'s pin. Nothing moves; port order returns at the next start."""
        if self._pins.pop(index, None) is None:
            return False
        slots = list(self._slots)
        if index < len(slots):
            slots[index] = replace(slots[index], pinned=False)
        self._slots = _trimmed(slots)
        return True

    def set_pins(self, pins: Mapping[int, SlotPin]) -> None:
        """Take the pins as a whole, as a hand-edited configuration names them.

        Each change is a targeted move: a pinned socket's controller goes to its
        slot, a slot whose socket is empty shows missing, and whatever held it
        moves to the end. While seeding, the next frame simply seeds with them.
        """
        if self._seeding:
            self._pins = dict(pins)
            return
        for index in [i for i in self._pins if pins.get(i) != self._pins[i]]:
            self.unpin(index)
        for index, pin in sorted(pins.items()):
            if self._pins.get(index) == pin:
                continue
            live = next((s for s in self._slots if s.state == CONNECTED and s.key == pin.key), None)
            if live is not None and live.local_id is not None:
                self.pin(index, live.kind, live.local_id)
                continue
            self._place_absent_pin(index, pin)

    def _place_absent_pin(self, index: int, pin: SlotPin) -> None:
        slots = list(self._slots) + [_EMPTY] * (index + 1 - len(self._slots))
        occupant = slots[index]
        if occupant.state != EMPTY:
            slots.append(replace(occupant, pinned=False))
        slots[index] = _pinned_absent(pin)
        self._pins[index] = pin
        self._slots = _trimmed(slots)

    def forget(self, index: int) -> bool:
        """Silence a missing slot: it keeps its position but drives no marker and raises no notes."""
        if not 0 <= index < len(self._slots) or self._slots[index].state != MISSING:
            return False
        slots = list(self._slots)
        slots[index] = replace(slots[index], state=RESERVED, notes=())
        self._slots = tuple(slots)
        return True
