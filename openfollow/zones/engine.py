# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Zone occupancy tracking and OSC dispatch with hysteresis and debounce."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openfollow.configuration import OscDestinationsConfig
from openfollow.osc.parser import coerce_osc_args, tokenize_osc_message
from openfollow.zones.geometry import point_in_polygon, shrink_polygon

if TYPE_CHECKING:
    from openfollow.configuration import TriggerZoneConfig, TriggerZonesConfig
    from openfollow.osc.service import OscService

logger = logging.getLogger(__name__)

# (entity_kind, id) – e.g. ("marker", 0) or ("detection", 17)
EntityId = tuple[str, int]
# (entity_id, world_x, world_y)
EntityPos = tuple[EntityId, float, float]
# Opaque structural fingerprint for a zone – only used for dict-key
# equality across config reloads (see ``_zone_signature``).
ZoneSignature = tuple[Any, ...]


@dataclass
class ZoneOccupancy:
    """Runtime state for a single zone across evaluations."""

    zone_index: int
    occupants: set[EntityId] = field(default_factory=set)
    last_event_time: float = 0.0
    last_event_address: str = ""  # last fired OSC address for diagnostics
    shrunken_vertices: list[tuple[float, float]] = field(default_factory=list)  # cached for hysteresis
    original_vertices: list[tuple[float, float]] = field(default_factory=list)  # static geometry cache
    triggered_by_set: frozenset[int] = frozenset()  # entity kinds that can trigger this zone

    @property
    def count(self) -> int:
        return len(self.occupants)


class ZoneEngine:
    """Polygon-membership marker with OSC-on-transition semantics."""

    def __init__(
        self,
        config: TriggerZonesConfig,
        osc_service: OscService,
        destinations: OscDestinationsConfig | None = None,
    ) -> None:
        self._osc = osc_service
        self._occupancy: list[ZoneOccupancy] = []
        self._config: TriggerZonesConfig = config
        # Shared OSC destination profiles a zone's ``destination_id`` resolves
        # against. Empty until provided so an unselected zone emits nothing.
        self._destinations: OscDestinationsConfig = destinations or OscDestinationsConfig(destinations=[])
        self._states_snapshot: tuple[tuple[int, bool, int], ...] = ()  # immutable snapshot for web thread
        self._diagnostics_snapshot: tuple[dict[str, Any], ...] = ()  # diagnostics snapshot for web thread
        self.reload_config(config, destinations)

    def reload_config(
        self,
        config: TriggerZonesConfig,
        destinations: OscDestinationsConfig | None = None,
    ) -> None:
        """Replace configuration; preserve occupancy for structurally unchanged zones.

        ``destinations`` stages the OSC destination profiles zone sends
        resolve against; ``None`` keeps the previously staged set.
        """
        if destinations is not None:
            self._destinations = destinations
        # Multiple zones can share the same signature (identical vertices,
        # trigger_source, enabled) – a stacked duplicate the user created for
        # any reason. Store a FIFO queue per signature so each new zone
        # inherits from at most one prior zone and we don't clobber occupancy
        # across identical zones.
        old_occupancy_by_sig: dict[ZoneSignature, list[ZoneOccupancy]] = {}
        for occ in self._occupancy:
            # pragma: no branch – the False arm requires an occupancy
            # whose zone_index is out of range in the OLD config (the
            # one still installed at this point), which can't happen
            # in normal flow because every occupancy is created
            # against the current config and reload swaps it later.
            if occ.zone_index < len(self._config.zones):  # pragma: no branch
                old_zone = self._config.zones[occ.zone_index]
                sig = _zone_signature(old_zone)
                old_occupancy_by_sig.setdefault(sig, []).append(occ)

        self._config = config
        self._occupancy = []
        # When globally disabled, don't carry occupancy across the reload.
        # Otherwise re-enabling later would see stale sets and either miss
        # /first for entities already inside (engine thinks they're still
        # occupants) or emit spurious /partial|/final for entities that have
        # since left. update() also short-circuits while disabled, so this
        # config-reload boundary is the only place the transition is observed.
        carry_occupancy = config.enabled
        for idx, zone in enumerate(config.zones):
            sig = _zone_signature(zone)
            queue = old_occupancy_by_sig.get(sig)
            prev = queue.pop(0) if queue else None
            occ = ZoneOccupancy(zone_index=idx)
            if carry_occupancy and prev is not None:
                occ.occupants = set(prev.occupants)
                occ.last_event_time = prev.last_event_time
                occ.last_event_address = prev.last_event_address
            verts = [(float(v[0]), float(v[1])) for v in zone.vertices if len(v) >= 2]
            occ.original_vertices = verts
            occ.shrunken_vertices = self._compute_shrunken(verts)
            occ.triggered_by_set = frozenset(zone.triggered_by)
            self._occupancy.append(occ)
        self._refresh_states_snapshot()

    def _compute_shrunken(self, verts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(verts) < 3:
            return verts
        return shrink_polygon(verts, self._config.hysteresis)

    def update(
        self,
        marker_positions: Iterable[EntityPos],
        detection_positions: Iterable[EntityPos],
    ) -> None:
        """Evaluate all enabled zones against the supplied entity positions."""
        if not self._config.enabled or not self._occupancy:
            return

        markers = list(marker_positions)
        detections = list(detection_positions)
        now = time.monotonic()

        for occ in self._occupancy:
            zone = self._config.zones[occ.zone_index]
            if not zone.enabled:
                if occ.occupants:
                    occ.occupants.clear()
                # Reset debounce so the next enable+enter is not silently
                # swallowed by the prior session's timestamp.
                occ.last_event_time = 0.0
                # Also clear the address so the Diagnostics tab doesn't
                # render a stale "Last fired: /addr" after the zone has been
                # disabled. ``last_event_time = 0.0`` already gates the "X ago"
                # display, but the address span renders whenever the value is
                # truthy, so it has to be cleared explicitly.
                occ.last_event_address = ""
                continue

            source = zone.trigger_source
            candidates: list[EntityPos] = []
            if source in ("markers", "both"):
                candidates.extend(markers)
            if source in ("detection", "both"):
                candidates.extend(detections)

            # Per-marker ``triggered_by`` filter. Empty list passes everything
            # through (current behaviour); non-empty restricts which markers can
            # trigger this zone. Detection IDs are NEVER filtered here – operators
            # chose this scope because the detection ID space is upstream-of-frame
            # (re-identification, tracking churn) and filtering it would mean
            # different things at different points in the frame pipeline. Filter
            # applies after source-list merge so ``trigger_source=both`` with a
            # non-empty filter produces markers ∩ triggered_by) ∪ (all detections).
            if occ.triggered_by_set:
                allowed = occ.triggered_by_set
                candidates = [c for c in candidates if c[0][0] != "marker" or c[0][1] in allowed]

            verts = occ.original_vertices
            if len(verts) < 3:
                # A zone that is degenerate at reload time never has occupants
                # to clear: vertices are snapshotted on reload, so ``len(verts) < 3``
                # is fixed for the occupancy's lifetime, and occupants are only ever
                # assigned *after* this ``continue``. (Contrast the disabled-zone
                # branch above, which clears occupants because ``enabled`` is read
                # live and a populated zone can be disabled mid-life.)
                occ.last_event_time = 0.0
                # The address has to be cleared too or the Diagnostics
                # tab keeps showing a stale value after the polygon
                # was edited down to fewer than 3 vertices.
                occ.last_event_address = ""
                continue

            new_set = _evaluate_zone(candidates, verts, occ.shrunken_vertices, occ.occupants)
            # Only advance occupancy when the transition was actually emitted.
            # If debounce suppressed it, hold the old set so the membership
            # change is re-evaluated next tick rather than silently swallowed –
            # otherwise the receiver can be told the wrong entity is present.
            if self._emit_transitions(occ, zone, new_set, now):
                occ.occupants = new_set
        self._refresh_states_snapshot()

    def _emit_transitions(
        self,
        occ: ZoneOccupancy,
        zone: TriggerZoneConfig,
        new_set: set[EntityId],
        now: float,
    ) -> bool:
        """Emit OSC for membership changes between ``occ.occupants`` and
        ``new_set``. Returns ``True`` when the caller should advance
        ``occ.occupants`` to ``new_set`` (no change, or change emitted) and
        ``False`` when the change was debounce-suppressed and must be held for
        re-evaluation on the next tick.
        """
        entered = new_set - occ.occupants
        exited = occ.occupants - new_set
        if not entered and not exited:
            return True

        # Resolve the zone's destination. A blank or dangling
        # ``destination_id`` means "no target" – track membership for the
        # overlay (advance occupancy) but emit nothing.
        dest = self._destinations.get(zone.destination_id)
        if dest is None:
            return True

        debounce_s = max(0.0, self._config.debounce_ms / 1000.0)
        if debounce_s > 0.0 and (now - occ.last_event_time) < debounce_s:
            return False

        prev_count = len(occ.occupants)
        count = prev_count

        host = dest.host
        port = dest.port
        protocol = dest.protocol
        framing = dest.framing

        last_address: str = ""
        # Handle entries first, then exits – the order matters for first/final
        # classification when an entity enters and another exits in the same
        # evaluation. Sort for deterministic tie-breaking (set iteration is
        # insertion-ordered per CPython but insertion order here comes from
        # upstream set-difference, which is not stable across runs).
        for _ in sorted(entered):
            if count == 0:
                addr = self._send_zone_osc(zone.osc_address_first_entry, host, port, protocol, framing)
            else:
                addr = self._send_zone_osc(zone.osc_address_additional_entry, host, port, protocol, framing)
            if addr:
                last_address = addr
            count += 1

        for _ in sorted(exited):
            count -= 1
            if count == 0:
                addr = self._send_zone_osc(zone.osc_address_final_exit, host, port, protocol, framing)
            else:
                addr = self._send_zone_osc(zone.osc_address_partial_exit, host, port, protocol, framing)
            if addr:
                last_address = addr

        if last_address:
            occ.last_event_time = now
            occ.last_event_address = last_address
        return True

    def _send_zone_osc(self, raw: str, host: str, port: int, protocol: str, framing: str) -> str:
        """Tokenise a zone OSC field and forward to the service.

        Each ``osc_address_*_entry`` is one freeform string the operator
        typed in the zone editor. We parse it with the shared
        :func:`openfollow.osc.parser.tokenize_osc_message` so the
        ``address arg1 arg2 "foo bar"`` syntax matches OSC Output.
        Numeric coercion at the wire boundary mirrors what the OSC Output
        renderer does via :func:`openfollow.osc.template._typed_literal`,
        so a typed ``"1.5"`` reaches the receiver as OSC ``f`` rather than
        ``s``.

        Returns the OSC address actually sent (so the caller can record
        it as ``last_event_address`` for the Diagnostics tab) or ``""``
        when nothing was sent. Empty / whitespace-only fields and unclosed-quote
        fields both return ``""`` without firing:

        - Empty: same lenient contract as the legacy code path – an
          unset zone OSC field is a "skip this transition", not an
          error.
        - Unclosed quote: defence-in-depth. The web-form blur validator
          catches this on Save, but a hand-edited TOML or a programmatic
          POST that bypassed validation can still smuggle a malformed
          value to disk. Skip the send (rather than crashing the eval
          tick or sending garbage) and log once per occurrence; the
          ``OscService.send`` site otherwise has no guard for this.
        """
        if not raw:
            return ""
        try:
            address, str_args = tokenize_osc_message(raw)
        except ValueError:
            logger.warning(
                "Zone OSC field has unclosed quote (skipping send): %r",
                raw,
            )
            return ""
        if not address:
            return ""
        self._osc.send(
            address,
            args=coerce_osc_args(str_args),
            host=host,
            port=port,
            protocol=protocol,
            framing=framing,
        )
        return address

    def get_zone_states(self) -> list[tuple[int, bool, int]]:
        """Return per-zone (index, is_occupied, count) snapshots for overlay/UI.

        Safe to call from any thread: returns a copy of an immutable tuple
        built at the end of the most recent ``update()`` / ``reload_config()``.
        """
        return list(self._states_snapshot)

    def get_zone_diagnostics(self, index: int) -> dict[str, Any] | None:
        """Per-zone diagnostics snapshot.

        Returns ``None`` for an out-of-range index (lets the route layer
        return 404 without separately validating). Otherwise returns the
        per-zone diagnostics dict prepared at the end of the most recent
        ``update()`` / ``reload_config()``: occupants list (sorted for
        deterministic JSON output), occupancy summary, and last-fired
        event metadata. ``last_event_time`` is seconds since the engine
        started (``time.monotonic`` epoch is process-relative); the
        route layer can convert to wall-clock or relative-to-now.

        Safe to call from any thread. An earlier version iterated
        ``occ.occupants`` (a ``set``) directly from the web thread while
        ``update()`` mutated it on the main thread, which can raise
        ``RuntimeError: Set changed size during iteration`` under load. The
        snapshot dance below mirrors what ``_states_snapshot`` already does
        for ``get_zone_states`` – iteration happens once on the main thread
        inside ``_refresh_states_snapshot``; the web-thread read just hands
        back the frozen dict.
        """
        # Read the snapshot tuple once: update()/reload_config() can rebind it
        # on the main thread between the bounds check and the index access.
        snapshot = self._diagnostics_snapshot
        if index < 0 or index >= len(snapshot):
            return None
        # Shallow copy so a caller mutating the dict can't bleed into the snapshot.
        return dict(snapshot[index])

    def _refresh_states_snapshot(self) -> None:
        """Rebuild the frozen states + diagnostics tuples for cross-thread readers."""
        self._states_snapshot = tuple(
            (occ.zone_index, bool(occ.occupants), len(occ.occupants)) for occ in self._occupancy
        )
        # Build the diagnostics dicts here (main thread, post-update) so
        # ``get_zone_diagnostics`` never iterates a live set under web-thread
        # load. Sort occupants deterministically so a polling client diffing
        # two responses sees real changes, not iteration-order churn.
        #
        # ``last_event_age_seconds`` is computed server-side: ``time.monotonic``
        # is process-relative so the raw ``last_event_time`` is meaningless
        # to a browser. Subtracting from the snapshot-time ``now`` here
        # turns it into a "how long ago" value the editor can render
        # directly. ``-1.0`` signals "no event recorded yet" so the
        # client can render "No events yet." without re-checking the
        # address field.
        snapshot_now = time.monotonic()
        diagnostics: list[dict[str, Any]] = []
        for occ in self._occupancy:
            if occ.last_event_time > 0.0:
                age = max(0.0, snapshot_now - occ.last_event_time)
            else:
                age = -1.0
            diagnostics.append(
                {
                    "is_occupied": bool(occ.occupants),
                    "count": len(occ.occupants),
                    "occupants": sorted(
                        ({"kind": kind, "id": eid} for (kind, eid) in occ.occupants),
                        key=lambda d: (d["kind"], d["id"]),
                    ),
                    "last_event_time": occ.last_event_time,
                    "last_event_age_seconds": age,
                    "last_event_address": occ.last_event_address,
                }
            )
        self._diagnostics_snapshot = tuple(diagnostics)


def _zone_signature(zone: TriggerZoneConfig) -> ZoneSignature:
    """Structural fingerprint used to carry occupancy across config reloads.

    Excludes mutable display fields (e.g. ``name``, ``color``) so harmless UI
    edits do not reset occupancy and spuriously retrigger ``first_entry``.
    Includes ``enabled`` because a disable→enable round-trip that happens
    entirely between two reloads (without any intervening ``update()`` to run
    the per-zone cleanup) would otherwise preserve stale occupancy and
    suppress the /first transition the user expects on re-enable.

    Includes ``triggered_by``: narrowing the filter while a now-disallowed
    marker is already inside would otherwise preserve that marker as an
    occupant across reload, and the engine wouldn't fire a fresh
    ``/first_entry`` for the next allowed marker that walks in. Treating
    the filter as part of the semantic identity forces a fresh occupancy
    set when the operator changes which markers can trigger this zone.
    ``frozenset`` because list ordering carries no semantic meaning here
    – ``[1, 2]`` and ``[2, 1]`` produce identical engine behaviour and
    shouldn't reset occupancy on a save round-trip.
    """
    vertex_tuples = tuple(tuple(v) for v in zone.vertices)
    triggered = frozenset(zone.triggered_by)
    return (vertex_tuples, zone.trigger_source, zone.enabled, triggered)


def _evaluate_zone(
    candidates: list[EntityPos],
    original_vertices: list[tuple[float, float]],
    shrunken_vertices: list[tuple[float, float]],
    previous_occupants: set[EntityId],
) -> set[EntityId]:
    """Return the set of entity IDs inside the zone this evaluation.

    An entity already inside uses the shrunken polygon (hysteresis); an entity
    that was outside must cross the full polygon boundary to count as entered.
    """
    use_hysteresis = bool(shrunken_vertices) and len(shrunken_vertices) >= 3
    result: set[EntityId] = set()
    for entity_id, x, y in candidates:
        if entity_id in previous_occupants and use_hysteresis:
            if point_in_polygon(x, y, shrunken_vertices):
                result.add(entity_id)
        else:
            if point_in_polygon(x, y, original_vertices):
                result.add(entity_id)
    return result
