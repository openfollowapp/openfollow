# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Bus of eight normalized 0-1 faders, plus per-controlled-marker faders.

Eight indexed faders (1..8) are driven by incoming MIDI. A parallel set of
per-controlled-marker faders (keyed by marker id) are driven by gamepad velocity.
Both feed the same consumers: OSC "Fader on change" trigger, [fader] / [markerfader]
placeholders, HUD display, and future feedback paths.

The bus normalises all sources (stick deflection, 0-127 MIDI bytes, OSC floats) to
a 0-1 invariant, owns the pickup-mode state machine, and routes change notifications
without consumers knowing about source details.

Public API:

- :meth:`apply_config`: Update names, defaults, sources, display flags. Resets
  pickup for changed sources; current values persist within a session.
- :meth:`value` / :meth:`name` / :meth:`is_picked_up` / :meth:`show_on_display`:
  Render and diagnostic reads (1..8 indexing).
- :meth:`set_from_midi_normalized`: Incoming MIDI (0-127 → 0-1). Pickup gate engages
  when hardware position crosses the stored fader value.
- :meth:`provision_marker_faders` / :meth:`marker_fader_value` /
  :meth:`set_marker_fader_from_velocity_delta`: Per-controlled-marker faders (keyed
  by marker id). Gamepad computes ``deflection × dt × (1 / max_speed_s)`` and drives
  the fader of the marker it controls; consumers pull the value at render time.
- :meth:`subscribe`: Fires on every value change. No rate-limiting.
- :meth:`source_for`: Snapshot a fader's source mapping for dispatch-layer routing.

Pickup logic (classic hardware fader engagement):

1. MIDI-sourced faders start un-picked-up; unsourced and marker faders start picked-up.
2. The first MIDI message after un-pickup establishes a baseline (need two samples to
   determine direction).
3. Each message checks if the segment from previous to new hardware value crosses the
   stored value. Both upward (a ≤ stored ≤ b) and downward (a ≥ stored ≥ b) sweeps
   count; equality counts as a crossing (hardware parked on stored value).
4. On crossing, the fader snaps to hardware and pickup flags on for the rest of the session.

Threading: One RLock guards all access. Subscribers fire on the caller's thread
(rtmidi callback or GTK animation tick). The bus snapshots the subscriber list under
the lock but releases it before calling subscribers, so slow subscribers don't block
other readers/writers. Not a delivery guarantee: a 100ms subscriber still costs 100ms.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from openfollow.configuration import (
    VIRTUAL_FADER_COUNT,
    VirtualFadersConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class _FaderState:
    """Internal: per-fader runtime state.

    Mutable on purpose – :class:`VirtualFaderBus` updates fields
    in-place under its lock rather than constructing fresh dataclasses
    on every set call (reduces GC pressure on the rtmidi callback path).
    """

    name: str
    default_value: float
    current_value: float
    show_on_display: bool
    source_kind: str
    source_patch: int
    source_midi_type: str
    source_midi_channel: int
    source_midi_number: int
    picked_up: bool
    last_hardware_value: float | None


@dataclass
class _MarkerFaderState:
    """Internal: per-marker fader runtime value (see :class:`VirtualFaderBus`).

    Marker faders are velocity-driven by the gamepad currently controlling
    the marker – no MIDI source, no pickup gate – so the state is just the
    live 0-1 value plus the default it seeded from at provision time.
    """

    current_value: float
    default_value: float


@dataclass(frozen=True)
class FaderSource:
    """Read-only snapshot of a fader's source mapping.

    Returned from :meth:`VirtualFaderBus.source_for` so the dispatch
    layer can decide which incoming MIDI events to forward to which
    fader without holding the bus's lock or peeking at private state.

    ``midi_number`` is ``None`` when ``midi_type`` is
    ``channel_pressure`` – the wire format carries one pressure value
    per channel with no per-message number, so the snapshot can't
    represent it as an integer without lying. The dispatch layer reads
    this directly: ``midi_number is None`` means "match any incoming
    channel_pressure on this channel", parallel to ``midi_channel ==
    0`` meaning "match any channel".
    """

    kind: str
    patch: int
    midi_type: str
    midi_channel: int
    midi_number: int | None


class VirtualFaderBus:
    """Bus of eight normalized 0-1 faders. See module docstring."""

    def __init__(
        self,
        faders_config: VirtualFadersConfig | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._faders: list[_FaderState] = []
        self._subscribers: list[Callable[[int, float], None]] = []
        # Per-controlled-marker faders, keyed by marker id (distinct from
        # the fixed 1..N indexed faders above). One per controlled marker,
        # driven by whichever gamepad currently controls that marker.
        # Provisioned from ``controlled_marker_ids`` via
        # :meth:`provision_marker_faders`.
        self._marker_faders: dict[int, _MarkerFaderState] = {}
        # Change subscribers for marker-fader channel, kept separate to avoid marker_id/fader_index confusion.
        # Callbacks see (marker_id, new_value); see subscribe_marker_fader.
        self._marker_subscribers: list[Callable[[int, float], None]] = []
        cfg = faders_config or VirtualFadersConfig()
        self._build_faders(cfg)

    def _build_faders(self, cfg: VirtualFadersConfig) -> None:
        """Materialise eight ``_FaderState`` objects from config.

        Startup-only: every fader resets to its persisted default;
        ``picked_up`` defaults to ``True`` for gamepad / unsourced
        faders (no pickup needed) and ``False`` for MIDI-sourced
        faders (the operator must move the hardware past the default
        before the bus accepts hardware values).
        """
        states: list[_FaderState] = []
        for cf in cfg.faders:
            states.append(
                _FaderState(
                    name=cf.name,
                    default_value=cf.default_value,
                    current_value=cf.default_value,
                    show_on_display=cf.show_on_display,
                    source_kind=cf.source_kind,
                    source_patch=cf.source_patch,
                    source_midi_type=cf.source_midi_type,
                    source_midi_channel=cf.source_midi_channel,
                    source_midi_number=cf.source_midi_number,
                    picked_up=cf.source_kind != "midi",
                    last_hardware_value=None,
                )
            )
        self._faders = states

    def apply_config(self, faders_config: VirtualFadersConfig) -> None:
        """Hot-reload-safe: update names, defaults, sources, display flags.

        Current runtime values persist within a session (only startup resets).
        A fader whose source mapping changed has its pickup state reset so the
        operator must re-engage the new hardware control. Unchanged sources keep
        their pickup state, so cosmetic config edits (rename, default tweak)
        don't disrupt a live MIDI session.
        """
        with self._lock:
            for i, cf in enumerate(faders_config.faders):
                if i >= len(self._faders):  # pragma: no cover – config pads to count
                    break
                state = self._faders[i]
                source_changed = (
                    state.source_kind != cf.source_kind
                    or state.source_patch != cf.source_patch
                    or state.source_midi_type != cf.source_midi_type
                    or state.source_midi_channel != cf.source_midi_channel
                    or state.source_midi_number != cf.source_midi_number
                )
                state.name = cf.name
                state.default_value = cf.default_value
                state.show_on_display = cf.show_on_display
                state.source_kind = cf.source_kind
                state.source_patch = cf.source_patch
                state.source_midi_type = cf.source_midi_type
                state.source_midi_channel = cf.source_midi_channel
                state.source_midi_number = cf.source_midi_number
                if source_changed:
                    state.picked_up = cf.source_kind != "midi"
                    state.last_hardware_value = None

    def _state(self, fader_index: int) -> _FaderState:
        """Resolve a 1-based index to a ``_FaderState``.

        Raises :class:`IndexError` for out-of-range values rather
        than silently returning ``self._faders[-1]`` – silent wrap
        would hide caller bugs and route MIDI events to the wrong
        fader.
        """
        if not 1 <= fader_index <= len(self._faders):
            raise IndexError(
                f"fader_index {fader_index} out of range 1..{len(self._faders)}",
            )
        return self._faders[fader_index - 1]

    @property
    def fader_count(self) -> int:
        return VIRTUAL_FADER_COUNT

    def value(self, fader_index: int) -> float:
        with self._lock:
            return self._state(fader_index).current_value

    def name(self, fader_index: int) -> str:
        """Display name. Falls back to ``"Fader N"`` when blank.

        The fallback lives here rather than in config so a freshly
        initialised :class:`VirtualFaderConfig` doesn't carry redundant
        defaults that the operator would then have to clear before
        renaming. Stays in sync with the operator's chosen number
        scheme even if v2 ever changes the count.
        """
        with self._lock:
            state = self._state(fader_index)
            return state.name or f"Fader {fader_index}"

    def is_picked_up(self, fader_index: int) -> bool:
        with self._lock:
            return self._state(fader_index).picked_up

    def show_on_display(self, fader_index: int) -> bool:
        with self._lock:
            return self._state(fader_index).show_on_display

    def source_for(self, fader_index: int) -> FaderSource:
        """Snapshot the fader's source mapping for the dispatch layer.

        ``midi_number`` is ``None`` for ``channel_pressure`` sources (which carry
        no per-message number), making the API explicit rather than burying a
        special case in the dispatcher. The persisted ``int`` field exists for
        TOML / web-form uniformity but is meaningless on the wire.
        """
        with self._lock:
            state = self._state(fader_index)
            midi_number: int | None = state.source_midi_number
            if state.source_midi_type == "channel_pressure":
                midi_number = None
            return FaderSource(
                kind=state.source_kind,
                patch=state.source_patch,
                midi_type=state.source_midi_type,
                midi_channel=state.source_midi_channel,
                midi_number=midi_number,
            )

    def set_from_midi_normalized(
        self,
        fader_index: int,
        hardware_value: float,
    ) -> None:
        """Apply an incoming MIDI value (0-1, after raw/127 normalise).

        Goes through the pickup gate: while ``picked_up == False``,
        the hardware value is recorded but not propagated to
        subscribers. Pickup engages on the first sample that crosses
        (or lands exactly on) the stored fader value. Once picked-up,
        every distinct hardware value updates the fader and notifies
        subscribers.
        """
        notify_value: float | None = None
        with self._lock:
            state = self._state(fader_index)
            hw = max(0.0, min(1.0, hardware_value))
            if state.picked_up:
                if hw != state.current_value:
                    state.current_value = hw
                    notify_value = hw
            else:
                if state.last_hardware_value is not None:
                    crossed = (
                        state.last_hardware_value <= state.current_value <= hw
                        or state.last_hardware_value >= state.current_value >= hw
                    )
                    if crossed:
                        # Only notify when the fader's value actually moves.
                        # Edge case: pickup engages by landing exactly on
                        # the stored value – ``state.current_value = hw``
                        # is then a no-op, and emitting a "changed" event
                        # for an unchanged value would break the bus's
                        # change-only contract.
                        previous = state.current_value
                        state.picked_up = True
                        state.current_value = hw
                        if hw != previous:
                            notify_value = hw
                state.last_hardware_value = hw
        if notify_value is not None:
            self._notify(fader_index, notify_value)

    # ------------------------------------------------------------------
    # Per-controlled-marker faders (keyed by marker id, not fader index)
    # ------------------------------------------------------------------

    def provision_marker_faders(
        self,
        marker_ids: Iterable[int],
        default_value: float = 0.0,
    ) -> None:
        """Sync the marker-fader set to ``marker_ids`` (one per controlled
        marker).

        New ids get a fader seeded at ``default_value``; ids no longer
        present are dropped. **Surviving ids keep their current value** –
        so reordering ``controlled_marker_ids`` (or adding / removing one)
        never disturbs the faders of the markers that stayed, and because
        the store is id-keyed (not index-keyed) concurrent controllers
        never clobber each other. Marker id 0 is reserved (PSN "ignored"
        sentinel) and non-ints from a hand-edited config are skipped
        defensively.
        """
        wanted = {mid for mid in marker_ids if isinstance(mid, int) and not isinstance(mid, bool) and mid >= 1}
        seed = max(0.0, min(1.0, default_value))
        with self._lock:
            for mid in wanted:
                if mid not in self._marker_faders:
                    self._marker_faders[mid] = _MarkerFaderState(
                        current_value=seed,
                        default_value=seed,
                    )
            for mid in list(self._marker_faders):
                if mid not in wanted:
                    del self._marker_faders[mid]

    def set_marker_fader_from_velocity_delta(
        self,
        marker_id: int,
        delta: float,
    ) -> None:
        """Integrate a gamepad velocity delta into one marker's fader.

        The caller computes ``delta = deflection × dt × (1 / max_speed_s)``
        so the bus stays free of controller-config knowledge; clamps to
        0-1. No-op when the marker has no provisioned fader (e.g. a
        controller pointed at a marker that isn't in
        ``controlled_marker_ids``). Velocity-driven, so there's no pickup
        gate – consumers pull :meth:`marker_fader_value` at render time
        (Stream-mode OSC + per-frame HUD).

        Notifies marker-fader change subscribers only when the value actually moves (avoids no-change events).
        """
        notify_value: float | None = None
        with self._lock:
            state = self._marker_faders.get(marker_id)
            if state is None:
                return
            new_value = max(0.0, min(1.0, state.current_value + delta))
            if new_value != state.current_value:
                state.current_value = new_value
                notify_value = new_value
        if notify_value is not None:
            self._notify_marker(marker_id, notify_value)

    def marker_fader_value(self, marker_id: int) -> float | None:
        """Current 0-1 value of a marker's fader, or ``None`` when the
        marker has no provisioned fader (lets the OSC resolver surface the
        standard 'not registered' skip)."""
        with self._lock:
            state = self._marker_faders.get(marker_id)
            return None if state is None else state.current_value

    def subscribe(
        self,
        callback: Callable[[int, float], None],
    ) -> Callable[[], None]:
        """Register a value-change subscriber. Returns an unsubscribe callable.

        Subscribers see ``(fader_index, new_value)`` whenever a fader moves to a
        distinct value. Throttling (e.g., rate selection) is the subscriber's
        responsibility; the bus emits every change.
        """
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def _notify(self, fader_index: int, value: float) -> None:
        with self._lock:
            subs = list(self._subscribers)
        # Iterate outside the lock – slow subscribers run synchronously on the
        # caller's thread (rtmidi callback / GTK tick), costing that thread wall-clock
        # time but not blocking other readers/writers. Subscribers needing true
        # non-blocking delivery should enqueue onto their own worker queue.
        for sub in subs:
            try:
                sub(fader_index, value)
            except Exception:
                logger.exception("Virtual fader subscriber raised")

    def subscribe_marker_fader(
        self,
        callback: Callable[[int, float], None],
    ) -> Callable[[], None]:
        """Register a marker-fader value-change subscriber. Returns an unsubscribe.

        Subscribers see ``(marker_id, new_value)`` whenever a controlled marker's
        fader moves to a distinct value. Separate from :meth:`subscribe` (indexed
        channel) so the callback's first arg is unambiguously a marker id, not a
        fader index. Throttling is the subscriber's responsibility.
        """
        with self._lock:
            self._marker_subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._marker_subscribers.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def _notify_marker(self, marker_id: int, value: float) -> None:
        with self._lock:
            subs = list(self._marker_subscribers)
        # Same lock discipline as :meth:`_notify`: snapshot under the lock,
        # call subscribers outside it so a slow one can't block bus readers.
        for sub in subs:
            try:
                sub(marker_id, value)
            except Exception:
                logger.exception("Marker fader subscriber raised")
