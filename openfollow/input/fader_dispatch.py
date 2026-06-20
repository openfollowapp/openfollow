# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""MIDI → virtual-fader dispatch layer.

Routes incoming :class:`~openfollow.input.midi.MidiEvent`s to every fader whose
source mapping matches. The bus's pickup gate and change-only notification handle
the rest.

Kept separate from :class:`~openfollow.input.faders.VirtualFaderBus` so the bus
stays free of MIDI imports. :class:`~openfollow.input.faders.FaderSource` (returned
by :meth:`VirtualFaderBus.source_for`) is the read-only contract between the two.

Matching rules:

- Only faders with ``source_kind == "midi"`` are candidates.
- ``patch == 0`` matches any patch; otherwise an exact match against ``event.patch_id``.
- ``midi_type`` is a hard match (e.g., ``control_change`` doesn't match ``channel_pressure``).
- ``midi_channel == 0`` matches any channel; otherwise exact 1..16 match.
- ``midi_number is None`` matches any number (channel_pressure has no per-message number).

The matched event's value (0..127) is normalised to 0..1 and passed to
:meth:`VirtualFaderBus.set_from_midi_normalized`.

Threading: Runs on rtmidi's listener thread; only calls into the internally-locked bus.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openfollow.input.faders import VirtualFaderBus
    from openfollow.input.midi import MidiEvent, MidiSubsystem


# MIDI value bytes are 0..127; normalise to the bus's 0..1 domain.
_MIDI_VALUE_MAX = 127.0


class MidiFaderDispatcher:
    """Routes matching MIDI events onto the virtual fader bus."""

    def __init__(self, bus: VirtualFaderBus) -> None:
        self._bus = bus
        self._unsub: Callable[[], None] | None = None

    def attach(self, midi: MidiSubsystem) -> None:
        """Subscribe to ``midi``'s event stream. Idempotent – re-attaching
        drops the previous subscription first so a hot-reload that re-runs
        the wiring can't leak duplicate callbacks (each would double-apply
        every event)."""
        self.detach()
        self._unsub = midi.subscribe(self.handle_midi_event)

    def detach(self) -> None:
        """Drop the MIDI subscription if attached. Safe to call when not
        attached (shutdown / re-attach)."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def handle_midi_event(self, event: MidiEvent) -> None:
        """Forward ``event`` to every fader whose source mapping matches.

        Runs on the rtmidi listener thread. Walks all faders rather than
        keeping a reverse index because the bus is only eight faders wide –
        an O(8) scan per event is far cheaper than the bookkeeping a
        source→fader map would need to stay correct across live source
        edits.
        """
        normalized = event.value / _MIDI_VALUE_MAX
        for index in range(1, self._bus.fader_count + 1):
            source = self._bus.source_for(index)
            if source.kind != "midi":
                continue
            if source.patch and source.patch != event.patch_id:
                continue
            if source.midi_type != event.type:
                continue
            if source.midi_channel and source.midi_channel != event.channel:
                continue
            if source.midi_number is not None and source.midi_number != event.number:
                continue
            self._bus.set_from_midi_normalized(index, normalized)
