# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the MIDI → virtual-fader dispatch layer.

Covers the match contract (patch / type / channel / number wildcards),
value normalisation, the attach/detach lifecycle, and an end-to-end pass
through a real :class:`VirtualFaderBus` (dispatch → pickup → value move).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from openfollow.input.fader_dispatch import MidiFaderDispatcher
from openfollow.input.faders import FaderSource, VirtualFaderBus
from openfollow.input.midi import MidiEvent

pytestmark = pytest.mark.unit


def _event(
    *,
    type: str = "control_change",
    channel: int = 1,
    number: int | None = 7,
    value: int = 127,
    patch_id: int = 1,
) -> MidiEvent:
    return MidiEvent(
        type=type,
        channel=channel,
        number=number,
        value=value,
        patch_id=patch_id,
        timestamp=0.0,
    )


def _source(
    *,
    kind: str = "midi",
    patch: int = 0,
    midi_type: str = "control_change",
    midi_channel: int = 0,
    midi_number: int | None = 7,
) -> FaderSource:
    return FaderSource(
        kind=kind,
        patch=patch,
        midi_type=midi_type,
        midi_channel=midi_channel,
        midi_number=midi_number,
    )


class _FakeBus:
    """Minimal bus stand-in recording ``set_from_midi_normalized`` calls."""

    def __init__(self, sources: dict[int, FaderSource]) -> None:
        self._sources = sources
        self.fader_count = len(sources)
        self.calls: list[tuple[int, float]] = []

    def source_for(self, index: int) -> FaderSource:
        return self._sources[index]

    def set_from_midi_normalized(self, index: int, value: float) -> None:
        self.calls.append((index, value))


class _FakeMidi:
    """Subscribe/unsubscribe recorder mirroring ``MidiSubsystem``."""

    def __init__(self) -> None:
        self.subscribers: list[Callable[[MidiEvent], None]] = []

    def subscribe(
        self,
        callback: Callable[[MidiEvent], None],
    ) -> Callable[[], None]:
        self.subscribers.append(callback)

        def _unsub() -> None:
            self.subscribers.remove(callback)

        return _unsub


# --------------------------------------------------------------------------
# Match contract
# --------------------------------------------------------------------------


class TestMatchContract:
    def test_matching_midi_source_receives_normalized_value(self) -> None:
        bus = _FakeBus({1: _source()})
        MidiFaderDispatcher(bus).handle_midi_event(_event(value=127))
        assert bus.calls == [(1, 1.0)]

    def test_value_byte_normalised_0_to_1(self) -> None:
        bus = _FakeBus({1: _source()})
        MidiFaderDispatcher(bus).handle_midi_event(_event(value=64))
        assert bus.calls[0][0] == 1
        assert abs(bus.calls[0][1] - 64 / 127) < 1e-9

    def test_non_midi_source_ignored(self) -> None:
        bus = _FakeBus({1: _source(kind="gamepad"), 2: _source(kind="")})
        MidiFaderDispatcher(bus).handle_midi_event(_event())
        assert bus.calls == []

    def test_patch_zero_matches_any_patch(self) -> None:
        bus = _FakeBus({1: _source(patch=0)})
        MidiFaderDispatcher(bus).handle_midi_event(_event(patch_id=9))
        assert bus.calls == [(1, 1.0)]

    def test_specific_patch_must_match(self) -> None:
        bus = _FakeBus({1: _source(patch=2)})
        disp = MidiFaderDispatcher(bus)
        disp.handle_midi_event(_event(patch_id=3))  # mismatch
        assert bus.calls == []
        disp.handle_midi_event(_event(patch_id=2))  # match
        assert bus.calls == [(1, 1.0)]

    def test_type_is_a_hard_match(self) -> None:
        bus = _FakeBus({1: _source(midi_type="control_change")})
        MidiFaderDispatcher(bus).handle_midi_event(
            _event(type="channel_pressure", number=None),
        )
        assert bus.calls == []

    def test_channel_zero_matches_any_channel(self) -> None:
        bus = _FakeBus({1: _source(midi_channel=0)})
        MidiFaderDispatcher(bus).handle_midi_event(_event(channel=11))
        assert bus.calls == [(1, 1.0)]

    def test_specific_channel_must_match(self) -> None:
        bus = _FakeBus({1: _source(midi_channel=5)})
        disp = MidiFaderDispatcher(bus)
        disp.handle_midi_event(_event(channel=6))  # mismatch
        assert bus.calls == []
        disp.handle_midi_event(_event(channel=5))  # match
        assert bus.calls == [(1, 1.0)]

    def test_number_none_matches_any_number(self) -> None:
        # channel_pressure: source.midi_number is None → match regardless of
        # the event's number (the wire carries none for that type).
        bus = _FakeBus({1: _source(midi_type="channel_pressure", midi_number=None)})
        MidiFaderDispatcher(bus).handle_midi_event(
            _event(type="channel_pressure", number=None),
        )
        assert bus.calls == [(1, 1.0)]

    def test_specific_number_must_match(self) -> None:
        bus = _FakeBus({1: _source(midi_number=7)})
        disp = MidiFaderDispatcher(bus)
        disp.handle_midi_event(_event(number=8))  # mismatch
        assert bus.calls == []
        disp.handle_midi_event(_event(number=7))  # match
        assert bus.calls == [(1, 1.0)]

    def test_multiple_matching_faders_all_fire(self) -> None:
        bus = _FakeBus(
            {
                1: _source(midi_number=7),
                2: _source(midi_number=7),
                3: _source(midi_number=8),  # no match
            }
        )
        MidiFaderDispatcher(bus).handle_midi_event(_event(number=7, value=127))
        assert bus.calls == [(1, 1.0), (2, 1.0)]


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


class TestLifecycle:
    def test_attach_subscribes_and_routes_events(self) -> None:
        bus = _FakeBus({1: _source()})
        midi = _FakeMidi()
        disp = MidiFaderDispatcher(bus)
        disp.attach(midi)
        assert len(midi.subscribers) == 1
        # The subscribed callback routes through to the bus.
        midi.subscribers[0](_event(value=127))
        assert bus.calls == [(1, 1.0)]

    def test_reattach_drops_previous_subscription(self) -> None:
        bus = _FakeBus({1: _source()})
        midi = _FakeMidi()
        disp = MidiFaderDispatcher(bus)
        disp.attach(midi)
        disp.attach(midi)  # must not leave two callbacks behind
        assert len(midi.subscribers) == 1

    def test_detach_removes_subscription(self) -> None:
        bus = _FakeBus({1: _source()})
        midi = _FakeMidi()
        disp = MidiFaderDispatcher(bus)
        disp.attach(midi)
        disp.detach()
        assert midi.subscribers == []

    def test_detach_without_attach_is_safe(self) -> None:
        disp = MidiFaderDispatcher(_FakeBus({1: _source()}))
        disp.detach()  # no-op, must not raise


# --------------------------------------------------------------------------
# End-to-end through the real bus (dispatch → pickup → value move)
# --------------------------------------------------------------------------


class TestEndToEndRealBus:
    def test_event_moves_a_midi_sourced_fader_after_pickup(self) -> None:
        from openfollow.configuration import (
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        # Fader 2 sourced from CC 7 on any channel of patch 1, starting at 0.
        faders = [
            VirtualFaderConfig(),  # fader 1 (gamepad slot)
            VirtualFaderConfig(
                name="Vol",
                default_value=0.0,
                source_kind="midi",
                source_patch=1,
                source_midi_type="control_change",
                source_midi_channel=0,
                source_midi_number=7,
            ),
        ]
        bus = VirtualFaderBus(faders_config=VirtualFadersConfig(faders=faders))
        disp = MidiFaderDispatcher(bus)

        # First event lands exactly on the stored value → engages pickup
        # without moving; second event then moves the fader to 1.0.
        disp.handle_midi_event(_event(channel=1, number=7, value=0))
        disp.handle_midi_event(_event(channel=1, number=7, value=127))
        assert bus.value(2) == 1.0

    def test_unmatched_event_leaves_faders_untouched(self) -> None:
        from openfollow.configuration import (
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        faders = [
            VirtualFaderConfig(),
            VirtualFaderConfig(
                default_value=0.3,
                source_kind="midi",
                source_patch=1,
                source_midi_type="control_change",
                source_midi_channel=0,
                source_midi_number=7,
            ),
        ]
        bus = VirtualFaderBus(faders_config=VirtualFadersConfig(faders=faders))
        disp = MidiFaderDispatcher(bus)
        # Wrong CC number → no match, fader stays at its default.
        disp.handle_midi_event(_event(channel=1, number=8, value=127))
        assert bus.value(2) == 0.3
