# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for :class:`VirtualFaderBus`.

Covers the substrate:

* Construction: defaults to eight faders at value 0.0 with display-name
  fallback ``"Fader N"``; custom config flows through cleanly.
* Index validation: 1-based public indexing; out-of-range raises
  ``IndexError`` rather than silently wrapping to ``self._faders[-1]``.
* ``set_from_midi_normalized``: clamps to 0-1; pickup gate (baseline
  → no-cross → cross-up / cross-down / cross-equal); after pickup
  every distinct value notifies; identical values are idempotent.
* Per-controlled-marker faders: ``provision_marker_faders`` (seed /
  add / drop, preserving survivors, skipping reserved/non-int ids),
  ``set_marker_fader_from_velocity_delta`` (clamp, no-op when
  unprovisioned), ``marker_fader_value`` (``None`` when unprovisioned).
* ``subscribe`` / unsubscribe: idempotent unsubscribe; multiple
  subscribers each receive; one raising doesn't break the others.
* ``apply_config``: name / default / show-on-display updates carry
  through; current runtime values are preserved on cosmetic edits;
  pickup resets when the source mapping changes; unsourced faders stay
  picked-up after an unrelated edit.
* ``source_for``: snapshots the active mapping for the dispatch layer.
"""

from __future__ import annotations

import pytest

from openfollow.configuration import (
    VIRTUAL_FADER_COUNT,
    VirtualFaderConfig,
    VirtualFadersConfig,
)
from openfollow.input.faders import (
    FaderSource,
    VirtualFaderBus,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Construction + simple reads
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_config_yields_eight_faders_at_zero(self) -> None:
        bus = VirtualFaderBus()
        assert bus.fader_count == VIRTUAL_FADER_COUNT
        for i in range(1, VIRTUAL_FADER_COUNT + 1):
            assert bus.value(i) == 0.0
            assert bus.name(i) == f"Fader {i}"
            assert bus.show_on_display(i) is False

    def test_custom_config_flows_through(self) -> None:
        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(
                    name="Volume",
                    default_value=0.5,
                    show_on_display=True,
                ),
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg)
        assert bus.value(1) == 0.5
        assert bus.name(1) == "Volume"
        assert bus.show_on_display(1) is True

    def test_midi_sourced_fader_starts_unpicked_up(self) -> None:
        # MIDI sources go on faders 2-8; fader 1 is reserved for the
        # gamepad stick.
        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),  # fader 1 – unsourced
                VirtualFaderConfig(  # fader 2 – MIDI-driven
                    source_kind="midi",
                    source_patch=1,
                    source_midi_number=7,
                ),
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg)
        assert bus.is_picked_up(2) is False

    def test_unsourced_fader_starts_picked_up(self) -> None:
        bus = VirtualFaderBus()
        assert bus.is_picked_up(1) is True

    def test_gamepad_sourced_fader_starts_picked_up(self) -> None:
        # Gamepad stick produces relative motion, not absolute
        # position – pickup does not apply to fader 1.
        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(source_kind="gamepad"),
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg)
        assert bus.is_picked_up(1) is True


class TestIndexValidation:
    def test_value_rejects_zero_index(self) -> None:
        bus = VirtualFaderBus()
        with pytest.raises(IndexError):
            bus.value(0)

    def test_value_rejects_index_past_count(self) -> None:
        bus = VirtualFaderBus()
        with pytest.raises(IndexError):
            bus.value(VIRTUAL_FADER_COUNT + 1)

    def test_value_rejects_negative_index(self) -> None:
        # Negative indexing must not silently wrap to the last fader –
        # routing MIDI to the wrong fader silently is a bug class
        # this guard exists to prevent.
        bus = VirtualFaderBus()
        with pytest.raises(IndexError):
            bus.value(-1)


# ---------------------------------------------------------------------------
# set_from_midi_normalized – pickup gate
# ---------------------------------------------------------------------------


class TestSetFromMidiPickup:
    # Fader 1 reserved for gamepad stick; MIDI tests use fader 2.
    _FADER = 2

    def _midi_bus(self, default_value: float = 0.5) -> VirtualFaderBus:
        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),  # fader 1 – unsourced (default)
                VirtualFaderConfig(  # fader 2 – MIDI-driven
                    default_value=default_value,
                    source_kind="midi",
                    source_patch=1,
                    source_midi_number=7,
                ),
            ]
        )
        return VirtualFaderBus(faders_config=cfg)

    def test_first_message_is_baseline_only(self) -> None:
        # First MIDI sample establishes a baseline – we don't yet
        # know which way the operator is moving so we can't decide
        # whether the segment crosses the stored value.
        bus = self._midi_bus(default_value=0.5)
        seen: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: seen.append((i, v)))
        bus.set_from_midi_normalized(self._FADER, 0.2)
        assert bus.is_picked_up(self._FADER) is False
        assert bus.value(self._FADER) == 0.5  # default unchanged
        assert seen == []

    def test_no_crossing_does_not_engage(self) -> None:
        bus = self._midi_bus(default_value=0.5)
        seen: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: seen.append((i, v)))
        bus.set_from_midi_normalized(self._FADER, 0.2)
        bus.set_from_midi_normalized(self._FADER, 0.3)  # below 0.5; no cross
        assert bus.is_picked_up(self._FADER) is False
        assert seen == []

    def test_crossing_upward_engages_pickup(self) -> None:
        bus = self._midi_bus(default_value=0.5)
        seen: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: seen.append((i, v)))
        bus.set_from_midi_normalized(self._FADER, 0.2)
        bus.set_from_midi_normalized(self._FADER, 0.6)  # crossed 0.5 going up
        assert bus.is_picked_up(self._FADER) is True
        assert bus.value(self._FADER) == 0.6
        assert seen == [(self._FADER, 0.6)]

    def test_crossing_downward_engages_pickup(self) -> None:
        bus = self._midi_bus(default_value=0.5)
        seen: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: seen.append((i, v)))
        bus.set_from_midi_normalized(self._FADER, 0.8)
        bus.set_from_midi_normalized(self._FADER, 0.4)  # crossed 0.5 going down
        assert bus.is_picked_up(self._FADER) is True
        assert bus.value(self._FADER) == 0.4
        assert seen == [(self._FADER, 0.4)]

    def test_landing_exactly_on_default_engages(self) -> None:
        # Operator parked the hardware right on the stored value;
        # cleanest possible engagement, must count as a crossing.
        bus = self._midi_bus(default_value=0.5)
        bus.set_from_midi_normalized(self._FADER, 0.2)
        bus.set_from_midi_normalized(self._FADER, 0.5)
        assert bus.is_picked_up(self._FADER) is True

    def test_pickup_engaging_on_equality_does_not_notify(self) -> None:
        # Pickup engages exactly on the stored value, so no change event is emitted.
        # Breaking the bus's change-only contract would violate the subscription guarantee.
        bus = self._midi_bus(default_value=0.5)
        seen: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: seen.append((i, v)))
        bus.set_from_midi_normalized(self._FADER, 0.2)
        bus.set_from_midi_normalized(self._FADER, 0.5)  # crosses stored, lands on it
        assert bus.is_picked_up(self._FADER) is True
        assert seen == []

    def test_after_pickup_distinct_values_notify(self) -> None:
        bus = self._midi_bus(default_value=0.5)
        seen: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: seen.append((i, v)))
        bus.set_from_midi_normalized(self._FADER, 0.2)
        bus.set_from_midi_normalized(self._FADER, 0.6)
        bus.set_from_midi_normalized(self._FADER, 0.7)
        bus.set_from_midi_normalized(self._FADER, 0.8)
        assert seen == [
            (self._FADER, 0.6),
            (self._FADER, 0.7),
            (self._FADER, 0.8),
        ]

    def test_after_pickup_identical_values_idempotent(self) -> None:
        # MIDI controllers often emit the same CC value twice when
        # the operator stops moving – bus must dedupe so subscribers
        # don't see redundant fires.
        bus = self._midi_bus(default_value=0.5)
        seen: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: seen.append((i, v)))
        bus.set_from_midi_normalized(self._FADER, 0.2)
        bus.set_from_midi_normalized(self._FADER, 0.6)
        bus.set_from_midi_normalized(self._FADER, 0.6)
        assert seen == [(self._FADER, 0.6)]

    def test_clamps_below_zero(self) -> None:
        bus = self._midi_bus(default_value=0.5)
        bus.set_from_midi_normalized(self._FADER, 0.2)
        bus.set_from_midi_normalized(self._FADER, -0.5)
        # -0.5 clamps to 0.0; segment 0.2→0.0 doesn't cross 0.5.
        assert bus.is_picked_up(self._FADER) is False

    def test_clamps_above_one(self) -> None:
        bus = self._midi_bus(default_value=0.5)
        bus.set_from_midi_normalized(self._FADER, 2.0)  # baseline at 1.0
        bus.set_from_midi_normalized(self._FADER, 1.5)  # still 1.0 after clamp
        # No motion after clamp; pickup not engaged because we never
        # crossed 0.5 (both samples are at 1.0 post-clamp, but the
        # baseline path engages on equality so this stays unpicked).
        assert bus.is_picked_up(self._FADER) is False


# ---------------------------------------------------------------------------
# Per-controlled-marker faders (provision / set / value)
# ---------------------------------------------------------------------------


class TestMarkerFaders:
    def test_provision_seeds_value(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([1, 2], default_value=0.3)
        assert bus.marker_fader_value(1) == pytest.approx(0.3)
        assert bus.marker_fader_value(2) == pytest.approx(0.3)

    def test_provision_default_zero(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([1])
        assert bus.marker_fader_value(1) == 0.0

    def test_provision_clamps_default(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([1], default_value=5.0)
        assert bus.marker_fader_value(1) == 1.0

    def test_unprovisioned_marker_value_is_none(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([1])
        assert bus.marker_fader_value(99) is None

    def test_provision_skips_reserved_and_non_ints(self) -> None:
        bus = VirtualFaderBus()
        # 0 (reserved), -1 (< 1), True (bool) are all skipped; only 2 lands.
        bus.provision_marker_faders([0, -1, True, 2])
        assert bus.marker_fader_value(2) == 0.0
        assert bus.marker_fader_value(0) is None

    def test_reprovision_preserves_survivors_adds_and_drops(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([1, 2], default_value=0.0)
        bus.set_marker_fader_from_velocity_delta(1, 0.4)
        bus.set_marker_fader_from_velocity_delta(2, 0.6)
        # Reorder, keep 1+2, add 3 – survivors keep value, 3 is seeded.
        bus.provision_marker_faders([2, 1, 3])
        assert bus.marker_fader_value(1) == pytest.approx(0.4)
        assert bus.marker_fader_value(2) == pytest.approx(0.6)
        assert bus.marker_fader_value(3) == 0.0
        # Drop 3.
        bus.provision_marker_faders([1, 2])
        assert bus.marker_fader_value(3) is None

    def test_set_marker_fader_clamps_both_ends(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([1], default_value=0.9)
        bus.set_marker_fader_from_velocity_delta(1, 0.5)
        assert bus.marker_fader_value(1) == 1.0
        bus.set_marker_fader_from_velocity_delta(1, -5.0)
        assert bus.marker_fader_value(1) == 0.0

    def test_set_marker_fader_unprovisioned_is_noop(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([1])
        # No fader for 99 → silent no-op (no raise), value stays None.
        bus.set_marker_fader_from_velocity_delta(99, 0.5)
        assert bus.marker_fader_value(99) is None


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    def test_multiple_subscribers_each_receive(self) -> None:
        bus = VirtualFaderBus()
        a: list[tuple[int, float]] = []
        b: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: a.append((i, v)))
        bus.subscribe(lambda i, v: b.append((i, v)))
        bus.set_from_midi_normalized(1, 0.5)
        assert len(a) == 1 and len(b) == 1

    def test_unsubscribe_stops_receiving(self) -> None:
        bus = VirtualFaderBus()
        seen: list[tuple[int, float]] = []
        unsubscribe = bus.subscribe(lambda i, v: seen.append((i, v)))
        unsubscribe()
        bus.set_from_midi_normalized(1, 0.5)
        assert seen == []

    def test_unsubscribe_idempotent(self) -> None:
        bus = VirtualFaderBus()
        unsubscribe = bus.subscribe(lambda i, v: None)
        unsubscribe()
        unsubscribe()  # second call must not raise

    def test_one_subscriber_raising_does_not_break_others(self) -> None:
        bus = VirtualFaderBus()
        good: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: (_ for _ in ()).throw(RuntimeError("boom")))
        bus.subscribe(lambda i, v: good.append((i, v)))
        bus.set_from_midi_normalized(1, 0.5)
        assert len(good) == 1


# ---------------------------------------------------------------------------
# subscribe_marker_fader – change channel for marker fader-on-change
# ---------------------------------------------------------------------------


class TestSubscribeMarkerFader:
    def test_receives_marker_id_and_value_on_change(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([5])
        seen: list[tuple[int, float]] = []
        bus.subscribe_marker_fader(lambda mid, v: seen.append((mid, v)))
        bus.set_marker_fader_from_velocity_delta(5, 0.4)
        assert seen == [(5, pytest.approx(0.4))]

    def test_no_event_when_clamped_value_unchanged(self) -> None:
        # Stick held against the top: the gamepad still applies a delta
        # every poll tick, but the clamped value doesn't move – so no
        # change event should fire (the change-only contract).
        bus = VirtualFaderBus()
        bus.provision_marker_faders([5], default_value=1.0)
        seen: list[tuple[int, float]] = []
        bus.subscribe_marker_fader(lambda mid, v: seen.append((mid, v)))
        bus.set_marker_fader_from_velocity_delta(5, 0.5)
        assert seen == []

    def test_unprovisioned_marker_emits_no_event(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([5])
        seen: list[tuple[int, float]] = []
        bus.subscribe_marker_fader(lambda mid, v: seen.append((mid, v)))
        bus.set_marker_fader_from_velocity_delta(99, 0.4)
        assert seen == []

    def test_unsubscribe_stops_receiving(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([5])
        seen: list[tuple[int, float]] = []
        unsubscribe = bus.subscribe_marker_fader(
            lambda mid, v: seen.append((mid, v)),
        )
        unsubscribe()
        bus.set_marker_fader_from_velocity_delta(5, 0.4)
        assert seen == []

    def test_unsubscribe_idempotent(self) -> None:
        bus = VirtualFaderBus()
        unsubscribe = bus.subscribe_marker_fader(lambda mid, v: None)
        unsubscribe()
        unsubscribe()  # second call must not raise

    def test_one_subscriber_raising_does_not_break_others(self) -> None:
        bus = VirtualFaderBus()
        bus.provision_marker_faders([5])
        good: list[tuple[int, float]] = []
        bus.subscribe_marker_fader(
            lambda mid, v: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        bus.subscribe_marker_fader(lambda mid, v: good.append((mid, v)))
        bus.set_marker_fader_from_velocity_delta(5, 0.4)
        assert len(good) == 1

    def test_marker_channel_distinct_from_indexed_channel(self) -> None:
        # A marker id and a fader index are both small ints; the two
        # channels must stay partitioned so neither sees the other's
        # events.
        bus = VirtualFaderBus()
        bus.provision_marker_faders([1])
        indexed: list[tuple[int, float]] = []
        marker: list[tuple[int, float]] = []
        bus.subscribe(lambda i, v: indexed.append((i, v)))
        bus.subscribe_marker_fader(lambda mid, v: marker.append((mid, v)))
        bus.set_marker_fader_from_velocity_delta(1, 0.4)
        bus.set_from_midi_normalized(1, 0.5)
        assert marker == [(1, pytest.approx(0.4))]
        assert indexed == [(1, pytest.approx(0.5))]


# ---------------------------------------------------------------------------
# apply_config – hot reload semantics
# ---------------------------------------------------------------------------


class TestApplyConfig:
    def test_name_update_carries_through(self) -> None:
        bus = VirtualFaderBus()
        cfg = VirtualFadersConfig(faders=[VirtualFaderConfig(name="Master")])
        bus.apply_config(cfg)
        assert bus.name(1) == "Master"

    def test_default_update_does_not_reset_current_value(self) -> None:
        # Values persist within a session – only startup resets. An
        # operator tweaking the default mid-session must not see the
        # live value snap.
        bus = VirtualFaderBus()
        bus.set_from_midi_normalized(1, 0.7)
        assert bus.value(1) == pytest.approx(0.7)
        new_cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(default_value=0.1),
            ]
        )
        bus.apply_config(new_cfg)
        assert bus.value(1) == pytest.approx(0.7)  # preserved

    def test_show_on_display_update_carries_through(self) -> None:
        bus = VirtualFaderBus()
        new_cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(show_on_display=True),
            ]
        )
        bus.apply_config(new_cfg)
        assert bus.show_on_display(1) is True

    def test_source_change_resets_pickup(self) -> None:
        # Operator was driving Fader 2 from CC 7; remaps to CC 8.
        # Pickup must reset so the new hardware control re-engages.
        cfg_old = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),
                VirtualFaderConfig(
                    source_kind="midi",
                    source_patch=1,
                    source_midi_number=7,
                ),
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg_old)
        # Engage pickup on the original source.
        bus.set_from_midi_normalized(2, 0.0)
        bus.set_from_midi_normalized(2, 1.0)
        assert bus.is_picked_up(2) is True
        # Operator remaps – pickup must reset.
        cfg_new = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),
                VirtualFaderConfig(
                    source_kind="midi",
                    source_patch=1,
                    source_midi_number=8,
                ),
            ]
        )
        bus.apply_config(cfg_new)
        assert bus.is_picked_up(2) is False

    def test_unrelated_edit_preserves_pickup(self) -> None:
        # Cosmetic rename must not disrupt a live MIDI session.
        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(
                    name="Volume",
                    source_kind="midi",
                    source_patch=1,
                    source_midi_number=7,
                ),
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg)
        bus.set_from_midi_normalized(1, 0.0)
        bus.set_from_midi_normalized(1, 1.0)
        assert bus.is_picked_up(1) is True
        # Same source, new name.
        cfg.faders[0].name = "Master Volume"
        bus.apply_config(cfg)
        assert bus.is_picked_up(1) is True

    def test_unsourced_to_midi_transition_resets_pickup(self) -> None:
        # Gamepad is reserved for fader 1 and midi for faders 2-8, so a
        # gamepad↔midi transition can't happen on the same fader. The
        # invariant covered here: when a non-MIDI fader gets remapped to
        # a MIDI source, pickup resets to ``False``.
        cfg_unsourced = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),  # fader 1
                VirtualFaderConfig(),  # fader 2 – unsourced
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg_unsourced)
        assert bus.is_picked_up(2) is True
        cfg_midi = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),  # fader 1
                VirtualFaderConfig(
                    source_kind="midi",
                    source_patch=1,
                    source_midi_number=7,
                ),
            ]
        )
        bus.apply_config(cfg_midi)
        assert bus.is_picked_up(2) is False

    def test_midi_to_unsourced_transition_marks_picked_up(self) -> None:
        cfg_midi = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),  # fader 1
                VirtualFaderConfig(
                    source_kind="midi",
                    source_patch=1,
                    source_midi_number=7,
                ),
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg_midi)
        cfg_unsourced = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),
                VirtualFaderConfig(),
            ]
        )
        bus.apply_config(cfg_unsourced)
        assert bus.is_picked_up(2) is True


# ---------------------------------------------------------------------------
# source_for snapshot
# ---------------------------------------------------------------------------


class TestSourceFor:
    def test_returns_full_mapping(self) -> None:
        # MIDI source on fader 2 (faders 1 is reserved for gamepad).
        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),
                VirtualFaderConfig(
                    source_kind="midi",
                    source_patch=1,
                    source_midi_type="control_change",
                    source_midi_channel=3,
                    source_midi_number=42,
                ),
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg)
        src = bus.source_for(2)
        assert isinstance(src, FaderSource)
        assert src.kind == "midi"
        assert src.patch == 1
        assert src.midi_type == "control_change"
        assert src.midi_channel == 3
        assert src.midi_number == 42

    def test_unsourced_fader_returns_blank_kind(self) -> None:
        bus = VirtualFaderBus()
        src = bus.source_for(1)
        assert src.kind == ""

    def test_channel_pressure_source_drops_midi_number_to_none(self) -> None:
        """``channel_pressure`` carries one pressure value per channel
        with no per-message number – persisted int field meaningless on
        wire. source_for reports None so dispatcher reads "match any
        incoming channel_pressure on this channel"."""
        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),
                VirtualFaderConfig(
                    source_kind="midi",
                    source_patch=1,
                    source_midi_type="channel_pressure",
                    source_midi_channel=2,
                    source_midi_number=42,  # persisted but should be dropped
                ),
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg)
        src = bus.source_for(2)
        assert src.midi_type == "channel_pressure"
        assert src.midi_channel == 2
        assert src.midi_number is None

    def test_key_pressure_source_keeps_midi_number(self) -> None:
        """``key_pressure`` carries a note number (which note's
        pressure is being reported), so ``source_for`` keeps the
        configured number – only ``channel_pressure`` drops it."""
        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(),
                VirtualFaderConfig(
                    source_kind="midi",
                    source_patch=1,
                    source_midi_type="key_pressure",
                    source_midi_channel=2,
                    source_midi_number=60,
                ),
            ]
        )
        bus = VirtualFaderBus(faders_config=cfg)
        src = bus.source_for(2)
        assert src.midi_type == "key_pressure"
        assert src.midi_number == 60
