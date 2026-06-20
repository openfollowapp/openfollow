# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``ZoneEngine``: occupancy transitions, hysteresis, debounce,
config hot-reload, per-marker filtering, and OSC arg typing."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from openfollow.zones.engine import ZoneEngine

pytestmark = pytest.mark.unit


# Minimal doubles matching the TriggerZoneConfig / TriggerZonesConfig API
# surface the engine reads, keeping the test independent of the
# configuration module.
@dataclass
class _ZoneCfg:
    name: str = ""
    vertices: list[list[float]] = field(default_factory=list)
    color: str = "#ff8000"
    trigger_source: str = "markers"
    # Per-marker filter; empty list means all markers trigger.
    triggered_by: list[int] = field(default_factory=list)
    osc_address_first_entry: str = "/first"
    osc_address_additional_entry: str = "/additional"
    osc_address_partial_exit: str = "/partial"
    osc_address_final_exit: str = "/final"
    osc_host: str = ""
    osc_port: int = 0
    enabled: bool = True


@dataclass
class _ZonesCfg:
    enabled: bool = True
    show_overlay: bool = True
    eval_fps: int = 10
    default_osc_host: str = "127.0.0.1"
    default_osc_port: int = 53000
    debounce_ms: int = 0
    hysteresis: float = 0.0
    zones: list[_ZoneCfg] = field(default_factory=list)


class _RecordingOsc:
    """Stand-in for the ``OscService`` zone-engine consumer.

    The engine resolves per-zone host/port (or falls back to the
    section's ``default_osc_*``) and calls ``send`` with explicit
    keyword args. The recorder normalises the call into an
    ``(address, host, port)`` triple. The engine resolves the
    fallback up front, so the resolved defaults (``"127.0.0.1"`` /
    ``53000`` per ``_ZonesCfg``) appear in ``sends`` rather than the
    empty / zero placeholders.
    """

    def __init__(self) -> None:
        self.sends: list[tuple[str, str, int]] = []
        # Parallel record that captures typed args list.
        self.sends_full: list[tuple[str, list, str, int]] = []

    def send(
        self,
        address: str,
        args: tuple = (),
        *,
        host: str,
        port: int,
        protocol: str = "udp",
    ) -> None:
        self.sends.append((address, host, port))
        self.sends_full.append((address, list(args), host, port))


SQUARE_CORNERS = [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0]]


def _marker(tid: int, x: float, y: float):
    return (("marker", tid), x, y)


def _detection(tid: int, x: float, y: float):
    return (("detection", tid), x, y)


def _zone(**kwargs) -> _ZoneCfg:
    kwargs.setdefault("vertices", [list(v) for v in SQUARE_CORNERS])
    return _ZoneCfg(**kwargs)


def _engine(zones: list[_ZoneCfg], **cfg_kwargs) -> tuple[ZoneEngine, _RecordingOsc]:
    cfg = _ZonesCfg(zones=zones, **cfg_kwargs)
    osc = _RecordingOsc()
    return ZoneEngine(cfg, osc), osc  # type: ignore[arg-type]


def test_get_zone_diagnostics_reads_snapshot_once() -> None:
    # A concurrent rebind can shrink _diagnostics_snapshot between the bounds
    # check and the index access; the read-once fix must not IndexError.
    class _Racing(ZoneEngine):
        @property
        def _diagnostics_snapshot(self) -> tuple[dict[str, Any], ...]:
            return self._snaps.pop(0) if getattr(self, "_snaps", None) else ()

        @_diagnostics_snapshot.setter
        def _diagnostics_snapshot(self, _value: object) -> None:
            pass  # absorb the engine's own assignments

    engine = _Racing(_ZonesCfg(zones=[_zone()]), _RecordingOsc())  # type: ignore[arg-type]
    # First read (the fix) sees len 2; a second read (pre-fix) would shrink to 1.
    engine._snaps = [({"zone_index": 0}, {"zone_index": 1}), ({"zone_index": 0},)]
    assert engine.get_zone_diagnostics(1) == {"zone_index": 1}


class TestFirstEntryAndFinalExit:
    def test_first_entry_fires_when_first_marker_enters_empty_zone(self) -> None:
        engine, osc = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]

    def test_final_exit_fires_when_last_marker_leaves(self) -> None:
        engine, osc = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends.clear()
        engine.update([_marker(0, 10.0, 10.0)], [])
        assert osc.sends == [("/final", "127.0.0.1", 53000)]


class TestAdditionalEntryAndPartialExit:
    def test_additional_entry_fires_for_second_marker(self) -> None:
        engine, osc = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends.clear()
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 3.0, 3.0)], [])
        assert osc.sends == [("/additional", "127.0.0.1", 53000)]

    def test_partial_exit_fires_when_one_of_many_leaves(self) -> None:
        engine, osc = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 3.0, 3.0)], [])
        osc.sends.clear()
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 10.0, 10.0)], [])
        assert osc.sends == [("/partial", "127.0.0.1", 53000)]


class TestTriggerSource:
    def test_markers_only_source_ignores_detections(self) -> None:
        engine, osc = _engine([_zone(trigger_source="markers")])
        engine.update([], [_detection(0, 2.0, 2.0)])
        assert osc.sends == []

    def test_detection_only_source_ignores_markers(self) -> None:
        engine, osc = _engine([_zone(trigger_source="detection")])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == []

    def test_both_source_accepts_either(self) -> None:
        engine, osc = _engine([_zone(trigger_source="both")])
        engine.update([_marker(0, 2.0, 2.0)], [_detection(1, 3.0, 3.0)])
        # Emission order is deterministic: /first for the zero-count
        # transition, then /additional for every further entry in the same
        # eval. Engine sorts the entered set, so order here does not depend
        # on Python set iteration.
        assert [s[0] for s in osc.sends] == ["/first", "/additional"]


class TestMultipleZones:
    def test_zones_are_independent(self) -> None:
        left = _zone(
            name="L",
            osc_address_first_entry="/L_first",
            osc_address_final_exit="/L_final",
            osc_address_additional_entry="/L_add",
            osc_address_partial_exit="/L_part",
        )
        right = _zone(
            name="R",
            vertices=[[6.0, 0.0], [10.0, 0.0], [10.0, 4.0], [6.0, 4.0]],
            osc_address_first_entry="/R_first",
            osc_address_final_exit="/R_final",
            osc_address_additional_entry="/R_add",
            osc_address_partial_exit="/R_part",
        )
        engine, osc = _engine([left, right])
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 8.0, 2.0)], [])
        addresses = sorted(s[0] for s in osc.sends)
        assert addresses == ["/L_first", "/R_first"]


class TestEmptyOscAddressesAreSkipped:
    def test_blank_address_does_not_emit(self) -> None:
        engine, osc = _engine([_zone(osc_address_first_entry="")])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == []

    def test_blank_additional_entry_does_not_emit(self) -> None:
        engine, osc = _engine([_zone(osc_address_additional_entry="")])
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends.clear()
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 2.5, 2.5)], [])
        assert osc.sends == []

    def test_blank_final_exit_does_not_emit(self) -> None:
        engine, osc = _engine([_zone(osc_address_final_exit="")])
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends.clear()
        engine.update([], [])
        assert osc.sends == []

    def test_blank_partial_exit_does_not_emit(self) -> None:
        engine, osc = _engine([_zone(osc_address_partial_exit="")])
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 2.5, 2.5)], [])
        osc.sends.clear()
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == []


class TestZoneOccupancyCount:
    def test_count_property_returns_occupant_set_size(self) -> None:
        """``ZoneOccupancy.count`` exposes the live set size – covers
        line 55."""
        from openfollow.zones.engine import ZoneOccupancy

        occ = ZoneOccupancy(zone_index=0)
        assert occ.count == 0
        # ``occupants`` is typed ``set[EntityId]`` where EntityId is a
        # ``(kind, id)`` tuple; use the production shape so the test
        # catches a regression that narrows the type.
        occ.occupants.add(("marker", 0))
        occ.occupants.add(("marker", 1))
        assert occ.count == 2


class TestDisabledGuards:
    def test_disabled_engine_does_not_emit(self) -> None:
        engine, osc = _engine([_zone()], enabled=False)
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == []

    def test_disabled_zone_does_not_emit(self) -> None:
        engine, osc = _engine([_zone(enabled=False)])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == []

    def test_degenerate_polygon_does_not_emit(self) -> None:
        zone = _zone(vertices=[[0.0, 0.0], [1.0, 0.0]])
        engine, osc = _engine([zone])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == []


class TestHysteresis:
    def test_shrunken_polygon_prevents_boundary_flicker(self) -> None:
        engine, osc = _engine([_zone()], hysteresis=0.5)
        # Enter near the edge; still clearly inside the original polygon.
        engine.update([_marker(0, 0.25, 2.0)], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]
        osc.sends.clear()
        # Still inside original, but now outside the shrunken polygon – with
        # hysteresis the entity is considered to have exited.
        engine.update([_marker(0, 0.25, 2.0)], [])
        assert osc.sends == [("/final", "127.0.0.1", 53000)]

    def test_no_hysteresis_keeps_entity_inside_on_repeat(self) -> None:
        engine, osc = _engine([_zone()], hysteresis=0.0)
        engine.update([_marker(0, 0.25, 2.0)], [])
        osc.sends.clear()
        engine.update([_marker(0, 0.25, 2.0)], [])
        assert osc.sends == []


class TestDebounce:
    def test_rapid_repeat_events_are_suppressed_within_window(self) -> None:
        engine, osc = _engine([_zone()], debounce_ms=1000)
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert len(osc.sends) == 1
        # Another transition arriving inside the debounce window is skipped.
        engine.update([_marker(0, 10.0, 10.0)], [])
        assert len(osc.sends) == 1

    def test_events_outside_window_are_emitted(self) -> None:
        engine, osc = _engine([_zone()], debounce_ms=1)
        engine.update([_marker(0, 2.0, 2.0)], [])
        time.sleep(0.01)
        engine.update([_marker(0, 10.0, 10.0)], [])
        assert [s[0] for s in osc.sends] == ["/first", "/final"]

    def test_suppressed_swap_is_not_silently_advanced(self) -> None:
        # Within the debounce window, an A-leaves/B-enters swap is suppressed.
        # The occupant set must NOT advance to {B} silently – otherwise the
        # receiver is never told about the swap and a later exit is
        # misattributed. When the window clears the real transition emits.
        engine, osc = _engine([_zone()], debounce_ms=50)
        engine.update([_marker(0, 2.0, 2.0)], [])  # A enters → /first
        assert [s[0] for s in osc.sends] == ["/first"]
        # Same-position swap inside the window: A gone, B present. Suppressed.
        engine.update([_marker(1, 2.0, 2.0)], [])
        assert [s[0] for s in osc.sends] == ["/first"]
        # Occupancy was held (still 1 occupant – the deferred set), not lost.
        assert engine.get_zone_states()[0] == (0, True, 1)
        diag = engine.get_zone_diagnostics(0)
        assert diag is not None
        assert diag["occupants"] == [{"kind": "marker", "id": 0}]
        # After the window clears, the held change is re-evaluated and emitted:
        # B is an additional entry, A a partial exit. A buggy silent-advance
        # would have swallowed both and emitted nothing here.
        time.sleep(0.06)
        engine.update([_marker(1, 2.0, 2.0)], [])
        assert [s[0] for s in osc.sends] == ["/first", "/additional", "/partial"]
        assert engine.get_zone_states()[0] == (0, True, 1)
        diag = engine.get_zone_diagnostics(0)
        assert diag is not None
        assert diag["occupants"] == [{"kind": "marker", "id": 1}]


class TestReloadConfig:
    def test_reload_preserves_occupancy_for_unchanged_zone(self) -> None:
        zone = _zone()
        engine, osc = _engine([zone])
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends.clear()
        # Reload with the same zone – occupancy carries over, no re-emit.
        new_cfg = _ZonesCfg(zones=[_zone()])
        engine.reload_config(new_cfg)  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == []

    def test_reload_resets_occupancy_for_changed_polygon(self) -> None:
        engine, osc = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends.clear()
        moved = _zone(vertices=[[100.0, 100.0], [104.0, 100.0], [104.0, 104.0], [100.0, 104.0]])
        engine.reload_config(_ZonesCfg(zones=[moved]))  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        # Marker is outside the moved zone; no emit. This also proves the
        # carried-over occupancy (which would've suppressed re-entry) was reset.
        assert osc.sends == []

    def test_reload_preserves_occupancy_when_only_name_changes(self) -> None:
        engine, osc = _engine([_zone(name="Zone 1")])
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends.clear()
        # Renaming a zone must not reset occupancy – otherwise the marker
        # already inside would spuriously retrigger /first on next update.
        renamed = _zone(name="Renamed")
        engine.reload_config(_ZonesCfg(zones=[renamed]))  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == []

    def test_reload_preserves_occupancy_across_duplicate_signature_zones(self) -> None:
        z0 = _zone(name="A")
        z1 = _zone(name="B")  # identical signature, different display name
        engine, osc = _engine([z0, z1])
        # Populate zone 0 with marker 0, zone 1 with marker 1.
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 2.5, 2.5)], [])
        # Both zones cover the same region → both hold both markers. Clear
        # osc sends so only retrigger-detection matters after reload.
        osc.sends.clear()

        # Reload with same topology; zone 0 keeps marker 0, zone 1 keeps
        # marker 1 conceptually. The concrete test is that neither zone
        # re-emits /first (which would indicate its occupancy was lost).
        engine.reload_config(_ZonesCfg(zones=[_zone(name="A"), _zone(name="B")]))  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 2.5, 2.5)], [])
        # Any /first emission means occupancy was dropped across the reload.
        assert all(s[0] != "/first" for s in osc.sends)
        # Snapshot should still show both zones occupied with the same counts
        # as before the reload (both markers in both zones).
        assert engine.get_zone_states() == [(0, True, 2), (1, True, 2)]

    def test_reload_zone_disable_enable_between_updates_retriggers_first(self) -> None:
        engine, osc = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends.clear()

        # Reload disabled, then re-enable – no update() in between.
        engine.reload_config(_ZonesCfg(zones=[_zone(enabled=False)]))  # type: ignore[arg-type]
        engine.reload_config(_ZonesCfg(zones=[_zone(enabled=True)]))  # type: ignore[arg-type]

        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]

    def test_reload_disabled_drops_carried_occupancy(self) -> None:
        engine, osc = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends.clear()

        # Disable globally via config reload.
        engine.reload_config(_ZonesCfg(zones=[_zone()], enabled=False))  # type: ignore[arg-type]
        # No OSC while disabled.
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == []
        # Zone snapshot reports empty even though the marker is still over it.
        assert engine.get_zone_states() == [(0, False, 0)]

        # Re-enable. The marker is still inside – engine must treat this as a
        # fresh /first, not a silent re-occupation.
        engine.reload_config(_ZonesCfg(zones=[_zone()], enabled=True))  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]


class TestZoneStates:
    def test_get_zone_states_reports_counts(self) -> None:
        engine, _ = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 3.0, 3.0)], [])
        states = engine.get_zone_states()
        assert states == [(0, True, 2)]

    def test_get_zone_states_returns_independent_copy(self) -> None:
        """Mutating the returned list must not affect the engine's snapshot."""
        engine, _ = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0)], [])
        states = engine.get_zone_states()
        states.clear()
        assert engine.get_zone_states() == [(0, True, 1)]

    def test_get_zone_states_reflects_latest_update(self) -> None:
        """Snapshot refreshes after each update() so readers see current state."""
        engine, _ = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert engine.get_zone_states() == [(0, True, 1)]
        engine.update([_marker(0, 10.0, 10.0)], [])
        assert engine.get_zone_states() == [(0, False, 0)]


class TestMutationTargetedEdgeCases:
    """Edge cases that kill specific mutants in mutation testing."""

    def test_triangular_zone_uses_hysteresis_once_occupied(self) -> None:
        # Kills ``_evaluate_zone`` mutants that relax the hysteresis
        # vertex-count guard from ``>= 3`` to ``> 3`` or ``>= 4``: with a
        # 3-vertex shrunken polygon those mutants would fall through to
        # the un-shrunk polygon and register a point just inside the
        # original boundary as still-occupying, even after hysteresis
        # should have kicked it out.
        triangle = [[0.0, 0.0], [6.0, 0.0], [3.0, 6.0]]
        engine, osc = _engine(
            [_zone(vertices=[list(v) for v in triangle])],
            hysteresis=1.0,
        )
        # First evaluation: marker at the centroid enters the zone.
        engine.update([_marker(0, 3.0, 2.0)], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]
        # Second evaluation: marker moves to a point that is inside the
        # original triangle but outside the shrunken one.  The point
        # (0.1, 0.1) sits inside the original boundary (it's on the
        # y ≥ 0 edge side) but the shrink pushes the bottom edge
        # upward by hysteresis=1.0, leaving (0.1, 0.1) outside.
        engine.update([_marker(0, 0.1, 0.1)], [])
        # Mutant behavior would keep the marker "inside" and emit
        # nothing further; correct behavior fires /final.
        assert ("/final", "127.0.0.1", 53000) in osc.sends

    def test_reload_preserves_last_event_time_across_identical_zones(self) -> None:
        # Kills the reload_config mutant that replaces
        # ``occ.last_event_time = prev.last_event_time`` with ``= None``:
        # the carried debounce state is observable only via the next
        # update()'s willingness to emit, so pin it with a real debounce.
        cfg = _ZonesCfg(
            zones=[_zone()],
            debounce_ms=10_000,  # 10 s – far longer than the test run
        )
        osc = _RecordingOsc()
        engine = ZoneEngine(cfg, osc)  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]
        # Hot-reload with a structurally identical zones list – the
        # signature matches so occupancy AND last_event_time carry.
        engine.reload_config(
            _ZonesCfg(
                zones=[_zone()],
                debounce_ms=10_000,
            ),
        )
        # Move the marker out of the zone.  Under the correct
        # implementation the zone is still in debounce, so /final is
        # suppressed.  The mutant (which lost last_event_time) would
        # treat the reloaded zone as never-emitted and fire /final.
        engine.update([], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]  # /final suppressed

    def test_reload_ignores_orphaned_occupancy_indices(self) -> None:
        # Kills the reload_config mutant that changes the bounds test
        # ``occ.zone_index < len(self._config.zones)`` to ``<=``: with
        # the mutant, a reload that drops a zone would try to index
        # ``self._config.zones[len]`` and raise IndexError.  The
        # original skips the out-of-range occupancy entry.
        initial = _ZonesCfg(zones=[_zone(), _zone()])
        osc = _RecordingOsc()
        engine = ZoneEngine(initial, osc)  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        # Drop one zone – the trailing occupancy entry now points past
        # the new zones list and must be filtered out without raising.
        engine.reload_config(_ZonesCfg(zones=[_zone()]))
        assert len(engine.get_zone_states()) == 1

    def test_degenerate_three_vertex_shape_is_still_a_zone(self) -> None:
        # Kills the _compute_shrunken and update() mutants that flip the
        # minimum-vertex guard from ``< 3`` to ``<= 3``: those mutants
        # would treat a valid triangular zone as degenerate and drop its
        # occupancy unconditionally.  A triangle with a marker at its
        # centroid must register as occupied.
        triangle = [[0.0, 0.0], [6.0, 0.0], [3.0, 6.0]]
        engine, osc = _engine([_zone(vertices=[list(v) for v in triangle])])
        engine.update([_marker(0, 3.0, 2.0)], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]
        assert engine.get_zone_states() == [(0, True, 1)]

    def test_debounce_divisor_is_exactly_one_thousand(self) -> None:
        # Kills the _emit_transitions mutant that scales debounce_ms by
        # 1001 instead of 1000 – i.e. treats milliseconds as if 1001 ms
        # = 1 s.  With debounce_ms = 100 and two entries 99 ms apart,
        # the mutant rounds the gap to below its (slightly wider)
        # debounce window and still suppresses the second emission, so
        # a boundary test at ``gap ≈ debounce`` is what differentiates.
        # Here we use debounce_ms = 1000 and a time delta of exactly
        # 1.0 s: original treats window_s == 1.0 so gap (1.0) is NOT
        # less than window → emits; mutant sets window_s = 1000/1001 ≈
        # 0.999 so gap (1.0) is still not < window → also emits.  To
        # force divergence, use gap = 0.9995 s: original window=1.0 →
        # 0.9995 < 1.0 suppresses; mutant window≈0.999 → 0.9995 is not
        # less than 0.999 → would emit.
        import openfollow.zones.engine as engine_mod

        real_monotonic = engine_mod.time.monotonic
        clock = [1000.0]

        def fake_monotonic() -> float:
            return clock[0]

        engine_mod.time.monotonic = fake_monotonic  # type: ignore[assignment]
        try:
            engine, osc = _engine([_zone()], debounce_ms=1000)
            engine.update([_marker(0, 2.0, 2.0)], [])
            assert osc.sends == [("/first", "127.0.0.1", 53000)]
            # Move out ~0.9995 s later – correct implementation
            # suppresses the /final event because 0.9995 < 1.0 s window.
            clock[0] = 1000.9995
            engine.update([], [])
            assert osc.sends == [("/first", "127.0.0.1", 53000)]
        finally:
            engine_mod.time.monotonic = real_monotonic  # type: ignore[assignment]

    def test_osc_port_is_forwarded_on_transition_emits(self) -> None:
        # Kills multiple _emit_transitions mutants that drop the
        # ``zone.osc_port`` argument from the ``self._osc.send(...)``
        # call (mutmut rewrites keyword-like argument lists by
        # removing trailing positional args).  Those mutants still
        # send SOMETHING so they go unkilled by "sends is not empty"
        # assertions – a test that pins the full (address, host, port)
        # triple catches them.
        zone = _zone(osc_host="10.0.0.1", osc_port=9000)
        engine, osc = _engine([zone])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == [("/first", "10.0.0.1", 9000)]
        engine.update([], [])
        assert osc.sends == [
            ("/first", "10.0.0.1", 9000),
            ("/final", "10.0.0.1", 9000),
        ]

    def test_additional_entry_forwards_port(self) -> None:
        # Companion to the port-forwarding test above – covers the
        # ``osc_address_additional_entry`` emit site, which is a
        # separate call that mutmut mutates independently.
        zone = _zone(osc_host="10.0.0.1", osc_port=9000)
        engine, osc = _engine([zone])
        engine.update([_marker(0, 2.0, 2.0)], [])
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 3.0, 3.0)], [])
        assert osc.sends == [
            ("/first", "10.0.0.1", 9000),
            ("/additional", "10.0.0.1", 9000),
        ]

    def test_partial_exit_forwards_port(self) -> None:
        # Covers the partial-exit emit site.
        zone = _zone(osc_host="10.0.0.1", osc_port=9000)
        engine, osc = _engine([zone])
        engine.update([_marker(0, 2.0, 2.0)], [])
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 3.0, 3.0)], [])
        engine.update([_marker(1, 3.0, 3.0)], [])
        assert osc.sends == [
            ("/first", "10.0.0.1", 9000),
            ("/additional", "10.0.0.1", 9000),
            ("/partial", "10.0.0.1", 9000),
        ]

    def test_disabled_zone_resets_debounce_to_numeric_zero(self) -> None:
        # Kills the update() mutant that sets ``occ.last_event_time =
        # None`` on disable instead of ``0.0``.  The numeric-zero form
        # is load-bearing: later code computes ``now -
        # occ.last_event_time`` which would TypeError on None.  The
        # mutant survived because the existing "disable clears
        # occupants" test never re-enabled the zone and re-triggered.
        zone = _zone()
        zones_cfg = _ZonesCfg(zones=[zone], debounce_ms=1000)
        osc = _RecordingOsc()
        engine = ZoneEngine(zones_cfg, osc)  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        # Disable the zone; update() clears occupancy and resets debounce.
        zone.enabled = False
        engine.update([_marker(0, 2.0, 2.0)], [])
        # Re-enable and move a marker in – must not crash on
        # ``now - last_event_time`` arithmetic.  With the mutant this
        # would raise TypeError: unsupported operand type(s) for -.
        zone.enabled = True
        engine.update([_marker(0, 2.0, 2.0)], [])

    def test_detection_source_accepts_detections_positive(self) -> None:
        # Kills the update() mutant that rewrites the string literal
        # ``"detection"`` into nonsense (``"XXdetectionXX"``): the
        # existing negative test proves markers don't fire when
        # ``trigger_source="detection"``, but it does NOT prove
        # detections DO fire – so the literal can be renamed without
        # observable effect.  This positive assertion nails it down.
        engine, osc = _engine([_zone(trigger_source="detection")])
        engine.update([], [_detection(0, 2.0, 2.0)])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]

    def test_disabled_zone_does_not_short_circuit_remaining_zones(self) -> None:
        # Kills the update() mutant that replaces ``continue`` with
        # ``break`` in the disabled-zone branch: with multiple zones
        # and the first one disabled, ``break`` would skip evaluation
        # of zone index 1+ so its /first would never fire.
        z0 = _zone(enabled=False)
        z1 = _zone(osc_address_first_entry="/zone1-first")
        engine, osc = _engine([z0, z1])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == [("/zone1-first", "127.0.0.1", 53000)]

    def test_debounce_window_is_strict_less_than(self) -> None:
        # Kills the _emit_transitions mutant that flips the debounce
        # comparator from ``<`` to ``<=``: at a gap *exactly* equal to
        # the debounce window the original emits, the mutant suppresses.
        # debounce_ms is chosen so the resulting float (``ms/1000.0``)
        # and a clock delta of the same value produce a bit-exact
        # equality – 100 ms fails this because 0.1 is not representable
        # in binary floating point.  250 ms is exactly representable.
        import openfollow.zones.engine as engine_mod

        real_monotonic = engine_mod.time.monotonic
        clock = [1000.0]

        def fake_monotonic() -> float:
            return clock[0]

        engine_mod.time.monotonic = fake_monotonic  # type: ignore[assignment]
        try:
            engine, osc = _engine([_zone()], debounce_ms=250)
            engine.update([_marker(0, 2.0, 2.0)], [])
            assert osc.sends == [("/first", "127.0.0.1", 53000)]
            # Exactly debounce_s later: original emits (gap not <
            # window), mutant suppresses (gap == window → <=).
            clock[0] = 1000.0 + 0.25
            engine.update([], [])
            assert osc.sends == [
                ("/first", "127.0.0.1", 53000),
                ("/final", "127.0.0.1", 53000),
            ]
        finally:
            engine_mod.time.monotonic = real_monotonic  # type: ignore[assignment]

    def test_degenerate_vertex_list_resets_debounce_to_numeric_zero(self) -> None:
        # Companion to the disabled-zone test – covers the second
        # ``occ.last_event_time = 0.0`` reset inside update()'s
        # degenerate-verts branch.  Without this the mutant
        # ``= None`` could slip through the vertices-too-few path.
        zones_cfg = _ZonesCfg(zones=[_zone()], debounce_ms=1000)
        osc = _RecordingOsc()
        engine = ZoneEngine(zones_cfg, osc)  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        # Mutate the zone's vertices in-place to a degenerate 2-vertex
        # shape so the update() degenerate branch fires.
        zones_cfg.zones[0].vertices = [[0.0, 0.0], [1.0, 0.0]]
        engine.update([_marker(0, 0.5, 0.0)], [])
        # Re-inflate the zone and confirm the next update doesn't
        # TypeError on arithmetic with the (mutant-set) None.
        zones_cfg.zones[0].vertices = [list(v) for v in SQUARE_CORNERS]
        engine.update([_marker(0, 2.0, 2.0)], [])


# ---------------------------------------------------------------------------
# Zone OSC fields parse address + arguments with numeric typing applied at
# the wire boundary. Tests cover all four send sites (first / additional /
# partial / final), the unclosed-quote defence, and the address-with-
# whitespace behaviour change.
# ---------------------------------------------------------------------------


class TestZoneOscArgs:
    def test_address_only_field_sends_no_args_regression_baseline(self) -> None:
        """Backwards compat: a plain address (no spaces) still sends
        with an empty args list. Existing configs / TOMLs continue to
        produce identical wire output."""
        engine, osc = _engine([_zone(osc_address_first_entry="/zone/enter")])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends_full == [("/zone/enter", [], "127.0.0.1", 53000)]

    def test_address_plus_plain_args_typed_at_wire_boundary(self) -> None:
        """A field like ``/cue/go 1 1.5 hello`` tokenises into one
        address and three args, with int / float / str classification
        applied so the receiver sees the right OSC typetags."""
        engine, osc = _engine(
            [
                _zone(osc_address_first_entry="/cue/go 1 1.5 hello"),
            ]
        )
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends_full == [
            ("/cue/go", [1, 1.5, "hello"], "127.0.0.1", 53000),
        ]

    def test_address_plus_quoted_arg_grandma3_regression(self) -> None:
        """Quote-aware tokenisation for zone OSC field. Without it, quoted
        args ship as literal address with embedded spaces (OSC invalid)."""
        engine, osc = _engine(
            [
                _zone(osc_address_first_entry='/cmd "Fadermaster Executor 202 At 100 Fade 1"'),
            ]
        )
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends_full == [
            ("/cmd", ["Fadermaster Executor 202 At 100 Fade 1"], "127.0.0.1", 53000),
        ]

    def test_additional_entry_field_parses_args(self) -> None:
        engine, osc = _engine(
            [
                _zone(osc_address_additional_entry='/zone/count 2 "extra person"'),
            ]
        )
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends_full.clear()
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 3.0, 3.0)], [])
        assert osc.sends_full == [
            ("/zone/count", [2, "extra person"], "127.0.0.1", 53000),
        ]

    def test_partial_exit_field_parses_args(self) -> None:
        engine, osc = _engine(
            [
                _zone(osc_address_partial_exit='/zone/leave 1 "still inside"'),
            ]
        )
        engine.update([_marker(0, 2.0, 2.0), _marker(1, 3.0, 3.0)], [])
        osc.sends_full.clear()
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends_full == [
            ("/zone/leave", [1, "still inside"], "127.0.0.1", 53000),
        ]

    def test_final_exit_field_parses_args(self) -> None:
        engine, osc = _engine(
            [
                _zone(osc_address_final_exit='/zone/empty "all clear" 0'),
            ]
        )
        engine.update([_marker(0, 2.0, 2.0)], [])
        osc.sends_full.clear()
        engine.update([_marker(0, 10.0, 10.0)], [])
        assert osc.sends_full == [
            ("/zone/empty", ["all clear", 0], "127.0.0.1", 53000),
        ]

    def test_unclosed_quote_skips_send_silently(self) -> None:
        """Defence-in-depth: a malformed value (unclosed quote) reaches
        the engine only via a hand-edited TOML or a programmatic POST
        that bypassed blur validation. Skip the send rather than
        crashing the eval tick – the warning is logged once per
        occurrence."""
        engine, osc = _engine(
            [
                _zone(osc_address_first_entry='/cmd "unclosed'),
            ]
        )
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends_full == []

    def test_whitespace_only_field_skips_send(self) -> None:
        """Blank / whitespace-only field preserves the legacy "skip
        this transition" contract – a zone with no first-entry address
        configured is still allowed to fire the other three sites."""
        engine, osc = _engine(
            [
                _zone(
                    osc_address_first_entry="   ",
                    osc_address_final_exit="/zone/exit 0",
                ),
            ]
        )
        engine.update([_marker(0, 2.0, 2.0)], [])
        # Empty first-entry: no send.
        assert osc.sends_full == []
        engine.update([_marker(0, 10.0, 10.0)], [])
        # But final-exit still fires with its (typed) args.
        assert osc.sends_full == [
            ("/zone/exit", [0], "127.0.0.1", 53000),
        ]

    def test_address_with_whitespace_now_splits_into_address_plus_arg(self) -> None:
        """Behaviour change worth pinning: a config that *accidentally*
        had whitespace in an OSC address (legacy parser sent
        ``"/foo bar"`` as a literal address, invalid per OSC 1.0 §2.1
        and dropped or misrouted by every receiver) now sends ``/foo``
        with arg ``"bar"``. Treated as a fix, not a regression."""
        engine, osc = _engine([_zone(osc_address_first_entry="/foo bar")])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends_full == [("/foo", ["bar"], "127.0.0.1", 53000)]


# ---------------------------------------------------------------------------
# Per-marker filter and diagnostics helpers
# ---------------------------------------------------------------------------


class TestTriggeredByFilter:
    def test_empty_filter_passes_every_marker(self) -> None:
        """Default behaviour – every marker triggers."""
        engine, osc = _engine([_zone()])
        engine.update([_marker(0, 2.0, 2.0)], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]

    def test_filter_allows_listed_marker(self) -> None:
        engine, osc = _engine([_zone(triggered_by=[0, 2])])
        engine.update([_marker(2, 2.0, 2.0)], [])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]

    def test_filter_blocks_unlisted_marker(self) -> None:
        engine, osc = _engine([_zone(triggered_by=[0, 2])])
        engine.update([_marker(7, 2.0, 2.0)], [])
        assert osc.sends == []

    def test_filter_does_not_apply_to_detections(self) -> None:
        """``triggered_by`` filters MARKERS only. Detection IDs always
        pass through, even if their numeric id isn't in the filter list."""
        engine, osc = _engine(
            [
                _zone(
                    trigger_source="both",
                    triggered_by=[0, 2],
                )
            ]
        )
        # detection id 7 is NOT in triggered_by, but detections aren't
        # filtered – should still trigger.
        engine.update([], [_detection(7, 2.0, 2.0)])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]

    def test_filter_combines_with_trigger_source_both(self) -> None:
        """``trigger_source=both`` + ``triggered_by=[0]`` → marker 0 fires,
        marker 7 doesn't, detection 7 still fires (separate ID space)."""
        engine, osc = _engine(
            [
                _zone(
                    trigger_source="both",
                    triggered_by=[0],
                )
            ]
        )
        # Marker 7 is filtered out; detection 7 passes; both occupy
        # the zone simultaneously. First entry fires once for the
        # detection (which arrives first per sorted-id ordering).
        engine.update([_marker(7, 2.0, 2.0)], [_detection(7, 2.5, 2.5)])
        # Only one send (the detection's first-entry); the filtered
        # marker doesn't add a second occupant.
        assert osc.sends == [("/first", "127.0.0.1", 53000)]

    def test_filter_keeps_detection_when_trigger_source_is_detection(self) -> None:
        """A non-empty ``triggered_by`` on a detection-only zone is a
        no-op – there are no markers to filter, so every detection
        passes."""
        engine, osc = _engine(
            [
                _zone(
                    trigger_source="detection",
                    triggered_by=[0],
                )
            ]
        )
        engine.update([], [_detection(99, 2.0, 2.0)])
        assert osc.sends == [("/first", "127.0.0.1", 53000)]


class TestZoneDiagnostics:
    def test_get_zone_diagnostics_zero_state(self) -> None:
        engine, _osc = _engine([_zone()])
        diag = engine.get_zone_diagnostics(0)
        assert diag is not None
        assert diag["is_occupied"] is False
        assert diag["count"] == 0
        assert diag["occupants"] == []
        assert diag["last_event_time"] == 0.0
        assert diag["last_event_address"] == ""

    def test_get_zone_diagnostics_records_last_event_address(self) -> None:
        engine, _osc = _engine(
            [
                _zone(
                    osc_address_first_entry="/zone/enter 1",
                )
            ]
        )
        engine.update([_marker(0, 2.0, 2.0)], [])
        diag = engine.get_zone_diagnostics(0)
        assert diag is not None
        assert diag["last_event_address"] == "/zone/enter"
        assert diag["last_event_time"] > 0.0
        assert diag["occupants"] == [{"kind": "marker", "id": 0}]

    def test_get_zone_diagnostics_returns_none_for_out_of_range_index(self) -> None:
        engine, _osc = _engine([_zone()])
        assert engine.get_zone_diagnostics(99) is None
        assert engine.get_zone_diagnostics(-1) is None

    def test_get_zone_diagnostics_sorts_occupants_deterministically(self) -> None:
        """A polling client diff'ing two responses sees real changes
        only – iteration-order churn doesn't show up as a state delta."""
        engine, _osc = _engine([_zone(trigger_source="both")])
        engine.update(
            [_marker(2, 2.0, 2.0), _marker(0, 2.5, 2.5)],
            [_detection(5, 1.0, 1.0)],
        )
        diag = engine.get_zone_diagnostics(0)
        assert diag is not None
        assert diag["occupants"] == [
            {"kind": "detection", "id": 5},
            {"kind": "marker", "id": 0},
            {"kind": "marker", "id": 2},
        ]

    def test_disabled_zone_clears_last_event_address(self) -> None:
        """Disabled zones clear last_event_address from diagnostics."""
        zones_cfg = _ZonesCfg(zones=[_zone(osc_address_first_entry="/zone/enter")])
        engine = ZoneEngine(zones_cfg, _RecordingOsc())  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        # Sanity: the address was recorded.
        diag = engine.get_zone_diagnostics(0)
        assert diag is not None
        assert diag["last_event_address"] == "/zone/enter"
        # Disable the zone in place; the next update() should clear
        # both the timestamp and the address.
        zones_cfg.zones[0].enabled = False
        engine.update([_marker(0, 2.0, 2.0)], [])
        diag = engine.get_zone_diagnostics(0)
        assert diag is not None
        assert diag["last_event_address"] == ""
        assert diag["last_event_time"] == 0.0

    def test_degenerate_vertex_list_clears_last_event_address(self) -> None:
        """Degenerate zones clear last_event_address on config reload."""
        zones_cfg = _ZonesCfg(zones=[_zone(osc_address_first_entry="/zone/enter")])
        engine = ZoneEngine(zones_cfg, _RecordingOsc())  # type: ignore[arg-type]
        engine.update([_marker(0, 2.0, 2.0)], [])
        # New config instance with a degenerate (<3 vertex) polygon, as a
        # real hot-reload would deliver – distinct from the installed config
        # so reload compares old vs new signatures correctly.
        degenerate_cfg = _ZonesCfg(
            zones=[
                _zone(
                    osc_address_first_entry="/zone/enter",
                    vertices=[[0.0, 0.0], [1.0, 0.0]],
                )
            ]
        )
        engine.reload_config(degenerate_cfg)  # type: ignore[arg-type]
        engine.update([_marker(0, 0.5, 0.0)], [])
        diag = engine.get_zone_diagnostics(0)
        assert diag is not None
        assert diag["last_event_address"] == ""


class TestTriggeredByResetsOccupancyOnReload:
    def test_narrowing_filter_drops_now_disallowed_marker(self) -> None:
        """Narrowing filter resets occupancy; previously-allowed markers re-trigger."""
        from openfollow.zones.engine import _zone_signature  # noqa: PLC0415

        z_open = _zone(triggered_by=[])
        z_narrow = _zone(triggered_by=[0])  # marker 1 now disallowed
        # Signatures must differ so reload_config doesn't carry the
        # old occupancy set across.
        assert _zone_signature(z_open) != _zone_signature(z_narrow)

    def test_signature_treats_triggered_by_order_as_irrelevant(self) -> None:
        from openfollow.zones.engine import _zone_signature  # noqa: PLC0415

        a = _zone(triggered_by=[1, 2])
        b = _zone(triggered_by=[2, 1])
        assert _zone_signature(a) == _zone_signature(b)

    def test_reload_with_narrowed_filter_resets_occupancy(self) -> None:
        zones_cfg = _ZonesCfg(zones=[_zone(triggered_by=[0, 1])])
        engine = ZoneEngine(zones_cfg, _RecordingOsc())  # type: ignore[arg-type]
        engine.update(
            [_marker(0, 2.0, 2.0), _marker(1, 2.5, 2.5)],
            [],
        )
        # Both markers are inside.
        assert engine.get_zone_diagnostics(0)["count"] == 2  # type: ignore[index]
        # Narrow the filter so marker 1 is now disallowed.
        new_cfg = _ZonesCfg(zones=[_zone(triggered_by=[0])])
        engine.reload_config(new_cfg)  # type: ignore[arg-type]
        # Signature changed, occupancy reset to empty. The next
        # update() observes a fresh "first marker enters empty zone"
        # for marker 0 instead of suppressing it as a continuation.
        diag = engine.get_zone_diagnostics(0)
        assert diag is not None
        assert diag["count"] == 0
