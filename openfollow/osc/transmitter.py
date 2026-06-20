# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Flexible OSC transmitter runtime.

Operators define a list of OSC transmitter rows in
``OscTransmittersConfig``, each independently configurable (destination,
transport, source marker, message template, trigger). A single 60 Hz
scheduler thread ticks every enabled Stream row, renders its compiled
address + arg templates against the current marker state, and hands the
result to :class:`OscService` for transport.

- One shared scheduler thread, not one per row. Stream rows down-sample
  via a per-tick counter (``60 / trigger.rate_hz`` ticks per send), so
  rates of 1 / 5 / 10 / 20 / 30 / 60 Hz fire on cleanly-divisible
  cadences.
- Every row carries a ``trigger`` describing when to send. Stream is the
  rate-based behaviour above; Hotkey and ControllerButton triggers fire
  via the :class:`InputEventBus` path (handlers enqueue plans; the
  scheduler drains the queue on its next tick so the input thread never
  blocks on TCP I/O). MIDI and Fader triggers dispatch the same way. The
  EncoderOnChange kind round-trips in config but is inert.
- Skip-on-missing-placeholder: a row referencing an unresolvable
  placeholder (unregistered default marker, ``[x:N]`` for an
  unmapped N, etc.) skips the send and records the unresolved name in
  the per-binding ring buffer.
- Grid is read every tick via ``grid_provider`` so live ``GridConfig``
  edits take effect on the next send without a manager restart.
- Each row keeps the last ~100 send/skip events in a bounded deque.
  Complements (does not replace) the per-target ``ClientStats`` on
  :class:`OscService`.
- Per-row health lives on :class:`OscService` via
  ``service.stats_for(row.host, row.port, row.protocol, row.framing)``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from openfollow.configuration import (
    ControllerButtonTrigger,
    FaderOnChangeTrigger,
    HotkeyTrigger,
    MidiMessageTrigger,
    StreamTrigger,
)
from openfollow.osc.template import (
    CompiledTemplate,
    RenderContext,
    RenderError,
    compile_template,
    constant_arg_value,
    osc_arg_for,
    render,
    requires_default_marker,
)

if TYPE_CHECKING:
    from openfollow.configuration import (
        OscTransmitterConfig,
        OscTransmittersConfig,
    )
    from openfollow.input.events import (
        ButtonEvent,
        InputEventBus,
        KeyEvent,
    )
    from openfollow.input.faders import VirtualFaderBus
    from openfollow.input.midi import MidiEvent, MidiSubsystem
    from openfollow.osc.service import OscService
    from openfollow.psn.marker import Marker

logger = logging.getLogger(__name__)


# Must divide each entry of ``VALID_OSC_TRANSMITTER_RATES`` evenly so
# down-sample counters fire on integer-tick boundaries.
_SCHEDULER_HZ = 60
_TICK_INTERVAL_S = 1.0 / _SCHEDULER_HZ
# Shutdown join bound. Small because the scheduler's only blocking call
# is ``stop_event.wait(_TICK_INTERVAL_S)`` (~16 ms).
_SHUTDOWN_JOIN_S = 2.0


# Per-binding ring-buffer capacity. ~200 B/entry → ~20 kB per row.
_RING_BUFFER_CAPACITY = 100


_RingBufferStatus = Literal["sent", "skipped"]


@dataclass(frozen=True)
class RingBufferEntry:
    """One per-binding send-or-skip event recorded for diagnostics.

    ``status="sent"`` rows carry the rendered ``address`` / ``args``
    handed to ``OscService.send``. ``status="skipped"`` rows carry the
    skip cause (unresolved-placeholder name, or ``"default marker {N}
    not registered"`` for the default-marker miss) in ``error``.
    """

    ts: float
    address: str
    args: tuple[Any, ...]
    status: _RingBufferStatus
    error: str = ""


class BindingRingBuffer:
    """Thread-safe bounded FIFO of recent send / skip events for one row.

    Writers run on the scheduler / input-event threads; readers on the
    web-server thread. ``deque(maxlen=N)`` bounds growth: the N+1th write
    evicts the oldest.
    """

    def __init__(self, max_entries: int = _RING_BUFFER_CAPACITY) -> None:
        self._lock = threading.Lock()
        self._entries: deque[RingBufferEntry] = deque(maxlen=max_entries)

    def record_sent(self, address: str, args: list[Any]) -> None:
        """Record a successful ``OscService.send`` call."""
        entry = RingBufferEntry(
            ts=time.monotonic(),
            address=address,
            args=tuple(args),
            status="sent",
        )
        with self._lock:
            self._entries.append(entry)

    def record_skipped(self, error: str, address: str = "") -> None:
        """Record a skip. ``address`` is optional – a render-time skip
        may have no resolvable address; empty string is fine."""
        entry = RingBufferEntry(
            ts=time.monotonic(),
            address=address,
            args=(),
            status="skipped",
            error=error,
        )
        with self._lock:
            self._entries.append(entry)

    def snapshot(self) -> list[RingBufferEntry]:
        """Return a copy of the current entries, oldest first, so callers
        can iterate without holding the lock."""
        with self._lock:
            return list(self._entries)


class OscTransmitter:
    """Runtime state for a single transmitter row.

    Holds the snapshotted config plus the compiled address / arg
    templates so the renderer doesn't re-parse on every tick.
    ``update_config`` swaps both when a hot-reload edits the row
    in-place. The :class:`BindingRingBuffer` survives in-place
    ``update_config`` and is dropped only when the row's id leaves the
    config.
    """

    def __init__(self, cfg: OscTransmitterConfig) -> None:
        self.cfg: OscTransmitterConfig = cfg
        self.compiled_address: CompiledTemplate = compile_template(cfg.address)
        self.compiled_args: tuple[CompiledTemplate, ...] = tuple(compile_template(a) for a in cfg.args)
        self._recompute_derived()
        self.ring_buffer = BindingRingBuffer()

    def update_config(self, new_cfg: OscTransmitterConfig) -> None:
        """Replace this row's config + recompile its templates.
        Caller holds the manager's row lock."""
        self.cfg = new_cfg
        self.compiled_address = compile_template(new_cfg.address)
        self.compiled_args = tuple(compile_template(a) for a in new_cfg.args)
        self._recompute_derived()

    def _recompute_derived(self) -> None:
        """Cache values derived from the compiled templates so the fire
        path doesn't recompute invariants every tick:

        - ``needs_default_marker``: any template references the
          default-marker slot (drives the per-fire default-marker gate).
        - ``const_args``: pre-typed value for each arg that renders to a
          compile-time constant, ``None`` for per-fire args.

        Called from ``__init__`` and ``update_config`` only – the sole
        places the compiled templates change – so the caches can't drift.
        """
        self.needs_default_marker: bool = requires_default_marker(self.compiled_address) or any(
            requires_default_marker(a) for a in self.compiled_args
        )
        self.const_args: tuple[Any, ...] = tuple(constant_arg_value(a) for a in self.compiled_args)


# ``marker_provider(marker_id)`` returns the live ``Marker`` (whose
# ``pos`` property is GIL-atomic) or ``None`` when the id isn't mapped.
MarkerProvider = Callable[[int], "Marker | None"]
# ``grid_provider()`` returns ``(width, depth, max_height, z_offset)``
# from the live ``GridConfig``. Read every tick so a hot-reload of any
# grid dimension takes effect on the next send. ``[z.frac]`` /
# ``[z.frac.inv]`` rows require ``max_height > 0`` at render time, else
# the renderer raises ``RenderError`` and the ring buffer records the skip.
GridProvider = Callable[[], tuple[float, float, float, float]]
# Fader resolver for the fader family (``[fader]`` / ``[fader.scale:min-max]``
# / ``[fader.int:min-max]``).
# ``fader_provider(idx)`` returns the fader's current 0..1 value or
# ``None`` when the index isn't registered.
FaderProvider = Callable[[int], float | None]


# ``marker_fader_provider(marker_id)`` returns that marker's current
# 0..1 fader value for the ``[markerfader]`` placeholder, or ``None``
# when the marker has no provisioned fader.
MarkerFaderProvider = Callable[[int], float | None]


# ``controller_marker_provider(controller_idx)`` maps a 0-based controller
# index to the marker id it currently drives for the ``:cN`` reference, or
# ``None`` when the controller isn't connected / drives no marker.
ControllerMarkerProvider = Callable[[int], int | None]


@dataclass(frozen=True)
class _TickPlan:
    """Immutable per-row snapshot taken inside the manager's lock.

    Decouples the tick body from the live ``OscTransmitter`` so a
    concurrent ``restart()`` can't produce a torn send (e.g. new address
    with old args). Everything the renderer + service.send needs lives
    here. ``ring_buffer`` is a reference, not a copy – it is thread-safe
    and outlives any tick; a ``restart`` that drops this row's id leaves
    stale entries, but no thread reads a dropped row's buffer.
    """

    # ``None`` means no default marker picked; any default-marker
    # placeholder skips with ``"no default marker configured"`` until
    # set. Rows with no default-marker placeholder dispatch regardless.
    marker_id: int | None
    host: str
    port: int
    protocol: str
    framing: str
    compiled_address: CompiledTemplate
    compiled_args: tuple[CompiledTemplate, ...]
    ring_buffer: BindingRingBuffer
    # Cached on the row (see ``OscTransmitter._recompute_derived``) so
    # the fire path reads them instead of re-walking the compiled
    # templates every tick. ``needs_default_marker``: any default-marker
    # slot present. ``const_args``: per-arg pre-typed constant value
    # (``None`` where the arg renders per-fire), same length as
    # ``compiled_args``.
    needs_default_marker: bool
    const_args: tuple[Any, ...]
    # ``StreamTrigger.mode`` / ``min_change_m`` snapshot. ``stream_mode``
    # is ``"always"`` for non-Stream triggers; the on-change gate in
    # ``_fire_plan`` only fires when ``stream_mode == "on_change"`` AND
    # the row uses a default-marker placeholder. ``row_id`` indexes the
    # manager's ``_last_sent_pos`` cache (survives in-place
    # ``update_config``).
    row_id: str = ""
    stream_mode: str = "always"
    stream_min_change_m: float = 0.0
    # ``default_fader``: the row's 1-based default fader index; bare
    # ``[fader]`` / ``[fader.scale:min-max]`` / ``[fader.int:min-max]``
    # slots resolve through it. ``None`` (no default fader picked) makes bare slots raise
    # :class:`RenderError`. Snapshot at plan-build time so a concurrent
    # ``restart`` can't tear the render.
    #
    # ``event_value`` / ``event_velocity`` / ``event_note``: MIDI payload
    # populated by ``_handle_midi_event``; ``None`` for non-MIDI plans.
    # ``[value]`` / ``[velocity]`` / ``[note]`` referenced by a non-MIDI
    # plan raise :class:`RenderError`, recorded as a ring-buffer skip.
    default_fader: int | None = None
    event_value: int | None = None
    event_velocity: int | None = None
    event_note: int | None = None
    # The row this plan was built from, for an *identity* check only –
    # never read for mutable state (that's what the snapshot fields are
    # for). ``_fire_plan``'s on-change cache write uses it to refuse
    # priming a row that ``restart()`` dropped and re-created under the
    # same id during the send (``self._rows.get(row_id) is source_row``).
    source_row: OscTransmitter | None = None


@dataclass(frozen=True)
class _RenderResult:
    """Outcome of running a tick plan through the renderer without
    sending. ``skipped=True`` means the renderer / pre-send checks
    rejected the plan (default-marker miss, position read failure,
    render error, empty address) and ``error`` carries the cause;
    ``address`` / ``args`` are populated only when ``skipped=False``.

    Drives both the live tick path and the diagnostics ``preview_for`` /
    ``test_send`` endpoints so the diagnostic surface can't drift from
    what the runtime sends.
    """

    address: str
    args: tuple[Any, ...]
    skipped: bool
    error: str
    # The default-marker position the renderer used, or ``None`` when the
    # row has no default marker configured. ``_fire_plan`` reads it to
    # apply the ``mode == "on_change"`` gate. The gate watches only the
    # default marker, not message content.
    default_marker_pos: tuple[float, float, float] | None = None


class OscTransmitterManager:
    """Owns the row list, the 60 Hz scheduler thread, and the event-bus
    subscriptions for Hotkey / ControllerButton trigger dispatch.

    Lifecycle: ``start()`` spins the thread (idempotent), ``stop()``
    sets the stop event, joins, and unsubscribes from the event bus.
    ``restart(new_cfg)`` swaps the row list in place – the scheduler
    keeps running, only the rows it iterates change.

    Hot-reload: rows in the new config sharing an id with an existing
    row are updated in-place via ``update_config``; new ids get fresh
    :class:`OscTransmitter` instances; absent ids are dropped.

    Trigger dispatch:

    - Stream rows fire on the scheduler tick at their configured rate.
    - HotkeyTrigger / ControllerButtonTrigger rows fire on
      :class:`InputEventBus` events. :meth:`attach_event_bus` is the
      late-binding seam (``__init__`` runs before the bus exists).
      ``stop()`` unsubscribes so a torn-down manager sees no further
      events.
    - Event handlers do not render + send synchronously – they build a
      :class:`_TickPlan` and enqueue it; the scheduler drains the queue
      on its next tick. This keeps the input thread off the network: a
      TCP target's ~250 ms connect timeout cannot stall keyboard /
      gamepad polling. Event-send latency is bounded by one tick
      interval (~16 ms).
    - Both paths render through ``_fire_plan``, so ring-buffer logging
      and skip-on-missing-placeholder behaviour are identical.
    """

    def __init__(
        self,
        osc_service: OscService,
        marker_provider: MarkerProvider,
        grid_provider: GridProvider,
        fader_provider: FaderProvider | None = None,
        marker_fader_provider: MarkerFaderProvider | None = None,
        controller_marker_provider: ControllerMarkerProvider | None = None,
    ) -> None:
        self._service = osc_service
        self._marker_provider = marker_provider
        self._grid_provider = grid_provider
        # Optional so call sites that don't wire the virtual fader bus
        # still construct the manager. A fader-placeholder row without it
        # raises :class:`RenderError` at render time and skips.
        self._fader_provider = fader_provider
        # ``[markerfader]`` provider; optional for the same reason.
        self._marker_fader_provider = marker_fader_provider
        # ``:cN`` controller-reference provider; optional. A ``[markerid:c1]``
        # row without it raises :class:`RenderError` and skips.
        self._controller_marker_provider = controller_marker_provider
        self._lock = threading.Lock()
        self._rows: dict[str, OscTransmitter] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick: int = 0
        # Per-row last-sent default-marker position for ``mode ==
        # "on_change"``. Keyed by row id so a hot-reload keeps the cache
        # (no spurious extra send after reload). Cleared on ``restart()``
        # when the id is dropped or its ``marker_id`` (the gate signal)
        # changes. The gate watches only the default marker.
        self._last_sent_pos: dict[str, tuple[float, float, float]] = {}
        # Event-driven dispatch queue. Event handlers append plans here
        # under ``_lock``; the scheduler drains it each ``_tick_once``.
        # Keeps network I/O off the input thread.
        self._event_plans: list[_TickPlan] = []
        # Event-bus unsubscribe callables – ``stop()`` invokes both. The
        # bus is attached via :meth:`attach_event_bus` rather than passed
        # at construction because it doesn't exist yet at manager-init
        # time. The bus is owned by ``InputManager``, which stops after
        # the manager, so unsubscribing here races nothing.
        self._unsub_key: Callable[[], None] | None = None
        self._unsub_button: Callable[[], None] | None = None
        # MIDI / virtual fader subscriptions. Same late-binding seam as
        # the input event bus. ``stop()`` invokes the unsubscribes.
        self._unsub_midi: Callable[[], None] | None = None
        self._unsub_fader: Callable[[], None] | None = None
        # Marker-fader change channel: same bus, separate subscription
        # keyed by marker id (not fader index).
        self._unsub_marker_fader: Callable[[], None] | None = None
        # Per-row last-fire timestamp for FaderOnChange throttling.
        # Time-based, not tick-based, because event sources fire at
        # variable rates (a MIDI fader sweep emits ~127 messages over a
        # 1 s drag; an idle fader emits zero) – capping by elapsed time
        # gives stable Hz regardless of input burstiness. Keyed by row id
        # so a hot-reload keeps the throttle state; cleared on
        # ``restart()`` when the id is dropped.
        self._last_event_fire_ts: dict[str, float] = {}

    def attach_event_bus(self, event_bus: InputEventBus) -> None:
        """Subscribe the manager to an input event bus for Hotkey /
        ControllerButton trigger dispatch.

        Idempotent – the second call drops the previous subscription
        before installing the new one. Attached here rather than in
        ``__init__`` because the bus doesn't exist yet at manager-init
        time.
        """
        if self._unsub_key is not None:
            self._unsub_key()
        if self._unsub_button is not None:
            self._unsub_button()
        self._unsub_key = event_bus.subscribe_key(self._handle_key_event)
        self._unsub_button = event_bus.subscribe_button(
            self._handle_button_event,
        )

    def attach_midi_subsystem(self, midi: MidiSubsystem) -> None:
        """Subscribe the manager to a :class:`MidiSubsystem` for
        :class:`MidiMessageTrigger` dispatch.

        Same late-binding pattern as :meth:`attach_event_bus`.
        Idempotent – the previous subscription is dropped first so repeat
        calls during a hot-reload don't duplicate dispatch.
        """
        if self._unsub_midi is not None:
            self._unsub_midi()
        self._unsub_midi = midi.subscribe(self._handle_midi_event)

    def attach_virtual_fader_bus(self, bus: VirtualFaderBus) -> None:
        """Subscribe the manager to a :class:`VirtualFaderBus` for
        :class:`FaderOnChangeTrigger` dispatch.

        Same late-binding pattern as :meth:`attach_event_bus`.
        Idempotent. The bus emits ``(fader_index, new_value)`` on every
        distinct value change; the manager applies the per-row
        ``rate_hz`` throttle in :meth:`_handle_fader_change` because the
        bus is rate-agnostic.
        """
        if self._unsub_fader is not None:
            self._unsub_fader()
        self._unsub_fader = bus.subscribe(self._handle_fader_change)
        # Marker-fader channel: a FaderOnChangeTrigger with
        # ``marker_id >= 1`` fires here instead of the indexed channel.
        if self._unsub_marker_fader is not None:
            self._unsub_marker_fader()
        self._unsub_marker_fader = bus.subscribe_marker_fader(
            self._handle_marker_fader_change,
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def restart(self, cfg: OscTransmittersConfig) -> None:
        """Replace the row list. Existing scheduler thread keeps running.

        Rows are diffed by ``cfg.id``: an existing row whose id appears
        in the new config gets its config swapped via ``update_config``;
        a new id gets a fresh :class:`OscTransmitter`; an id absent from
        the new config is dropped from the next tick onward.
        """
        with self._lock:
            new_rows: dict[str, OscTransmitter] = {}
            invalidate_cache: set[str] = set()
            for row_cfg in cfg.transmitters:
                existing = self._rows.get(row_cfg.id)
                if existing is not None:
                    # Editing a surviving row's ``marker_id`` (gate
                    # signal) or trigger kind changes the cache entry's
                    # semantic basis – comparing the new marker position
                    # against the old cached value could wrongly skip the
                    # first send. Drop the cache entry on those edits;
                    # cosmetic edits (name / host / port / rate / address
                    # / args) leave it alone since the gate watches the
                    # marker, not the message.
                    old_cfg = existing.cfg
                    old_kind = type(old_cfg.trigger).__name__
                    new_kind = type(row_cfg.trigger).__name__
                    if old_cfg.marker_id != row_cfg.marker_id or old_kind != new_kind:
                        invalidate_cache.add(row_cfg.id)
                    existing.update_config(row_cfg)
                    new_rows[row_cfg.id] = existing
                else:
                    new_rows[row_cfg.id] = OscTransmitter(row_cfg)
            self._rows = new_rows
            # Drop on-change cache entries for removed rows; keep
            # surviving rows so a cosmetic-only reload doesn't trigger a
            # spurious next-tick send. Also drop entries flagged above
            # whose semantic basis changed.
            self._last_sent_pos = {
                rid: pos for rid, pos in self._last_sent_pos.items() if rid in new_rows and rid not in invalidate_cache
            }
            # Same pattern for the event-driven throttle cache. Rows whose
            # trigger kind or discriminator changed are also dropped
            # (flagged in ``invalidate_cache``) so the first event after
            # the reload isn't compared against a stale timestamp from the
            # previous trigger.
            self._last_event_fire_ts = {
                rid: ts
                for rid, ts in self._last_event_fire_ts.items()
                if rid in new_rows and rid not in invalidate_cache
            }

    def row_ids(self) -> list[str]:
        """Snapshot of currently-configured row ids. Order is not stable
        across reloads."""
        with self._lock:
            return list(self._rows.keys())

    def ring_buffer_for(self, row_id: str) -> list[RingBufferEntry] | None:
        """Snapshot of one row's recent send / skip events, oldest first.
        ``None`` for an unknown row id."""
        with self._lock:
            row = self._rows.get(row_id)
            if row is None:
                return None
            buffer = row.ring_buffer
        # Read outside the manager lock – the buffer has its own.
        return buffer.snapshot()

    # ------------------------------------------------------------------
    # Diagnostics surface
    #
    # Read-mostly endpoints feeding the per-binding diagnostics tab.
    # Each is a thin wrapper over the render pipeline so the
    # operator-visible state can't disagree with what the scheduler does.
    # ------------------------------------------------------------------

    def status_for(self, row_id: str) -> dict[str, Any] | None:
        """Per-row health snapshot for the Diagnostics tab. ``None`` for
        an unknown row id. JSON-serialisable as-is:

        * ``pps`` – sends-per-second over the last 1 s window, counted
          off the timestamped ring-buffer entries.
        * ``last_error`` – the most recent ``"skipped"`` entry's error,
          or ``None`` if the row has never skipped. Surfaced even when
          later sends succeeded.
        * ``healthy`` – ``True`` when the most recent entry is a send or
          there are no entries; ``False`` when it is a skip.
        * ``ring_buffer`` – last ~100 entries, oldest first, each a dict
          with ``ts`` / ``address`` / ``args`` / ``status`` / ``error``.
        """
        entries = self.ring_buffer_for(row_id)
        if entries is None:
            return None
        # ``time.monotonic`` matches the buffer's write clock, so the
        # window holds even when the wall clock jumps (NTP, suspend).
        now = time.monotonic()
        recent_sends = sum(1 for e in entries if e.status == "sent" and now - e.ts <= 1.0)
        # Most recent skip, reported even while sends are ongoing.
        last_error: str | None = None
        for entry in reversed(entries):
            if entry.status == "skipped":
                last_error = entry.error
                break
        if entries:
            healthy = entries[-1].status == "sent"
        else:
            healthy = True
        return {
            "pps": float(recent_sends),
            "last_error": last_error,
            "healthy": healthy,
            "ring_buffer": [
                {
                    "ts": e.ts,
                    "address": e.address,
                    "args": list(e.args),
                    "status": e.status,
                    "error": e.error,
                }
                for e in entries
            ],
        }

    def preview_for(self, row_id: str) -> dict[str, Any] | None:
        """Render ``row_id``'s current templates against current marker
        state and return the result without sending. ``None`` for an
        unknown row id."""
        plan = self._snapshot_plan_for(row_id)
        if plan is None:
            return None
        result = self._render_plan_pure(plan)
        return {
            "address": result.address,
            "args": list(result.args),
            "skipped": result.skipped,
            "error": result.error or None,
        }

    def test_send(self, row_id: str) -> dict[str, Any] | None:
        """Force a one-shot send for ``row_id`` regardless of its
        ``enabled`` flag. Returns the rendered shape + a ``sent`` flag,
        or ``None`` for an unknown row.

        Writes the same ring-buffer entry the live tick path would and
        obeys the same skip-on-no-data rules.
        """
        plan = self._snapshot_plan_for(row_id)
        if plan is None:
            return None
        # Reuse ``_fire_plan`` so the ring-buffer + service.send paths
        # stay identical to the live tick. ``is_test=True`` keeps the
        # probe out of the on-change cache – otherwise a test packet
        # would prime the gate and make the next live tick skip as
        # "unchanged" against the probed position.
        self._fire_plan(plan, is_test=True)
        # The most recent ring-buffer entry tells us what happened;
        # reading it rather than re-rendering keeps result and
        # diagnostics view consistent.
        entries = self.ring_buffer_for(row_id) or []
        if not entries:  # pragma: no cover - defensive (we just wrote)
            return {
                "sent": False,
                "address": "",
                "args": [],
                "error": "no ring-buffer entry recorded",
            }
        last = entries[-1]
        return {
            "sent": last.status == "sent",
            "address": last.address,
            "args": list(last.args),
            "error": last.error or None,
        }

    def _snapshot_plan_for(self, row_id: str) -> _TickPlan | None:
        """Build a tick plan for ``row_id`` under the manager lock so the
        diagnostics endpoints see the same atomic config snapshot the
        scheduler does. ``None`` for an unknown row id."""
        with self._lock:
            row = self._rows.get(row_id)
            if row is None:
                return None
            return self._plan_for_row(row)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spin the scheduler thread. Idempotent – a no-op while the
        thread is alive."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                daemon=True,
                name="OscTransmitter-Scheduler",
            )
            self._thread.start()

    def stop(self) -> bool:
        """Set the stop event, join the scheduler thread, and drop
        :class:`InputEventBus` subscriptions.

        Returns ``True`` if the scheduler is confirmed stopped (or never
        started), ``False`` if the join timed out and the worker is still
        running. Shutdown callers use this to decide whether it's safe to
        drain shared resources the scheduler may still use.

        ``_thread`` is cleared only once the join confirms exit. If the
        join times out, keep the reference so a subsequent ``start()``
        can't spin up a second scheduler alongside the live one.
        Unsubscribe runs unconditionally so event-driven dispatch stops
        even when the join times out.
        """
        # Set the stop flag before unsubscribing so an in-flight
        # ``emit_*`` (handlers already snapshotted, mid-fan-out) sees the
        # flag and short-circuits before queueing a plan – unsubscribe is
        # best-effort under the bus's snapshot-then-call dispatch.
        self._stop.set()
        if self._unsub_key is not None:
            self._unsub_key()
            self._unsub_key = None
        if self._unsub_button is not None:
            self._unsub_button()
            self._unsub_button = None
        # Drop MIDI / fader subscriptions on the same shutdown step so a
        # stopped manager sees no further events from them either.
        if self._unsub_midi is not None:
            self._unsub_midi()
            self._unsub_midi = None
        if self._unsub_fader is not None:
            self._unsub_fader()
            self._unsub_fader = None
        if self._unsub_marker_fader is not None:
            self._unsub_marker_fader()
            self._unsub_marker_fader = None
        # Drop plans queued by an event handler before the stop flag was
        # set, so a follow-up ``process_pending_events`` or ``_tick_once``
        # can't fire stale plans and break the post-stop "no further
        # sends" invariant.
        with self._lock:
            self._event_plans.clear()
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=_SHUTDOWN_JOIN_S)
        with self._lock:
            if self._thread is not thread:
                return True
            if thread.is_alive():
                logger.warning(
                    "OSC transmitter scheduler did not stop within %.3fs; keeping thread reference until it exits",
                    _SHUTDOWN_JOIN_S,
                )
                return False
            self._thread = None
            return True

    def __enter__(self) -> OscTransmitterManager:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        # Each iteration ticks every row whose down-sample counter fires,
        # then sleeps to the next beat. ``stop.wait`` returns early on
        # ``stop()``, so shutdown latency is bounded by one iteration.
        while not self._stop.is_set():
            self._tick_once()
            self._stop.wait(_TICK_INTERVAL_S)

    def _tick_once(self) -> None:
        """Run one scheduler beat. Snapshots an immutable
        :class:`_TickPlan` per firing row inside the lock, then renders +
        sends outside it, so a concurrent ``restart()`` can't tear a
        tick. Also drains the event-driven plan queue here so network I/O
        stays off the input thread.
        """
        plans = self._snapshot_plans()
        for plan in plans:
            # The renderer converts a missing value into RenderError, but a
            # provider/resolver or send can raise anything else; one row's
            # failure must not kill the thread and silence every other row.
            try:
                self._fire_plan(plan)
            except Exception as exc:
                plan.ring_buffer.record_skipped(error=f"internal error: {exc}")
                logger.exception("OSC row %s failed during tick", plan.row_id)

    def _snapshot_plans(self) -> list[_TickPlan]:
        with self._lock:
            tick = self._tick
            self._tick = tick + 1
            plans: list[_TickPlan] = []
            for row in self._rows.values():
                cfg = row.cfg
                if not cfg.enabled:
                    continue
                # Only Stream triggers fire on the scheduler tick; other
                # triggers fire via their event paths.
                trigger = cfg.trigger
                if not isinstance(trigger, StreamTrigger):
                    continue
                # ``rate_hz`` is clamped to the allowed set at save time
                # so this divides exact; ``max(1, ...)`` guards the
                # hand-edited TOML path.
                ticks_per_send = max(1, _SCHEDULER_HZ // trigger.rate_hz)
                if tick % ticks_per_send != 0:
                    continue
                plans.append(self._plan_for_row(row))
            # Drain queued event-driven plans onto this tick so they
            # render + send on the scheduler thread, not the emitting
            # input thread. The list swap under the lock is atomic.
            if self._event_plans:
                plans.extend(self._event_plans)
                self._event_plans.clear()
            return plans

    def process_pending_events(self) -> None:
        """Drain queued event-driven plans synchronously, without
        advancing the Stream-row tick counter. Production reaches the
        same point via the next scheduler iteration; this is the
        synchronous seam tests use to assert event-driven dispatch
        without coupling Stream-row cadence into the assertion.
        """
        with self._lock:
            plans = list(self._event_plans)
            self._event_plans.clear()
        for plan in plans:
            self._fire_plan(plan)

    def _plan_for_row(
        self,
        row: OscTransmitter,
        *,
        event_value: int | None = None,
        event_velocity: int | None = None,
        event_note: int | None = None,
    ) -> _TickPlan:
        """Build a :class:`_TickPlan` from a row's current config and
        compiled templates. Caller holds the manager lock – the plan
        captures everything the renderer + service.send need so the fire
        happens outside the lock without tearing on a concurrent
        ``restart``.

        The MIDI dispatch path populates ``event_value`` /
        ``event_velocity`` / ``event_note``; other plans pass ``None``,
        and any ``[value]`` / ``[velocity]`` / ``[note]`` reference in
        them raises :class:`RenderError` → ring-buffer skip.
        """
        cfg = row.cfg
        # Snapshot StreamTrigger mode + threshold so the ``on_change``
        # gate in ``_fire_plan`` needn't peek back into the
        # concurrently-mutable row config. Non-Stream triggers default to
        # ``"always"`` + 0.0, making the gate a no-op.
        trigger = cfg.trigger
        if getattr(trigger, "kind", "") == "stream":
            stream_mode = getattr(trigger, "mode", "always")
            stream_min_change_m = float(
                getattr(trigger, "min_change_m", 0.0),
            )
        else:
            stream_mode = "always"
            stream_min_change_m = 0.0
        return _TickPlan(
            marker_id=cfg.marker_id,
            host=cfg.host,
            port=cfg.port,
            protocol=cfg.protocol,
            framing=cfg.framing,
            compiled_address=row.compiled_address,
            compiled_args=row.compiled_args,
            ring_buffer=row.ring_buffer,
            row_id=cfg.id,
            stream_mode=stream_mode,
            stream_min_change_m=stream_min_change_m,
            default_fader=cfg.default_fader,
            event_value=event_value,
            event_velocity=event_velocity,
            event_note=event_note,
            needs_default_marker=row.needs_default_marker,
            const_args=row.const_args,
            source_row=row,
        )

    # ------------------------------------------------------------------
    # Event-bus dispatch
    # ------------------------------------------------------------------

    def _handle_key_event(self, event: KeyEvent) -> None:
        """Enqueue a plan for every :class:`HotkeyTrigger` row matching a
        key edge. Runs on the input thread.

        Match contract – ``key``, ``edge``, and ``modifiers`` all equal
        exactly. ``edge`` is not symmetric (a ``"press"`` row doesn't
        fire on release). ``modifiers`` is exact: Ctrl+F1 doesn't fire a
        plain-F1 row and vice versa.

        Multiple matching rows fire independently. Plan-building runs
        under the manager lock; plans land on ``self._event_plans`` and
        the scheduler renders + sends them on its next tick, keeping the
        input thread off the network.

        Early-return on ``_stop`` covers the in-flight emit window:
        :meth:`InputEventBus.emit_key` snapshots handlers before invoking
        them, so an event snapshotted before :meth:`stop` still reaches
        this handler – the check stops it queueing an undrained plan.
        """
        if self._stop.is_set():
            return
        with self._lock:
            for row in self._rows.values():
                cfg = row.cfg
                if not cfg.enabled:
                    continue
                trigger = cfg.trigger
                if not isinstance(trigger, HotkeyTrigger):
                    continue
                if trigger.key != event.key:
                    continue
                if trigger.edge != event.edge:
                    continue
                if frozenset(trigger.modifiers) != event.modifiers:
                    continue
                self._event_plans.append(self._plan_for_row(row))

    def _handle_button_event(self, event: ButtonEvent) -> None:
        """Enqueue a plan for every :class:`ControllerButtonTrigger` row
        matching a button edge. Runs on the input thread.

        Match contract mirrors :meth:`_handle_key_event`. Matchers ignore
        ``event.controller_index`` (no per-controller routing).
        """
        if self._stop.is_set():
            return
        with self._lock:
            for row in self._rows.values():
                cfg = row.cfg
                if not cfg.enabled:
                    continue
                trigger = cfg.trigger
                if not isinstance(trigger, ControllerButtonTrigger):
                    continue
                if trigger.button != event.button:
                    continue
                if trigger.edge != event.edge:
                    continue
                self._event_plans.append(self._plan_for_row(row))

    # ------------------------------------------------------------------
    # MIDI / fader event dispatch
    # ------------------------------------------------------------------

    def _handle_midi_event(self, event: MidiEvent) -> None:
        """Enqueue a plan for every :class:`MidiMessageTrigger` row
        matching an incoming MIDI message. Runs on the rtmidi listener
        thread.

        Match contract – fields wildcard on their empty / ``None``
        sentinel:

        - ``patch_id == 0`` matches any patch; else exact.
        - ``type`` always matches exactly (part of the row's identity).
        - ``channel is None`` matches any 1..16 channel; else exact.
        - ``number is None`` matches any number, including the ``None``
          emitted for ``program_change`` / ``channel_pressure`` (the
          trigger's post_init normalises ``number`` to ``None`` for
          those so the comparison is consistent); else exact.
        - ``value is None`` matches any value byte (CC value / note
          velocity / pressure / program byte); else exact.

        Multiple matching rows fire independently. Event-slot fields:

        - ``event_value`` is set for every event (CC value / pressure /
          program byte / note velocity, uniformly).
        - ``event_velocity`` / ``event_note`` are set only for
          ``note_on`` / ``note_off``; ``None`` otherwise. ``[velocity]``
          or ``[note]`` outside a note context raises
          :class:`RenderError` → ring-buffer skip.

        Early-return on ``_stop`` guards the in-flight emit window.
        """
        if self._stop.is_set():
            return
        with self._lock:
            for row in self._rows.values():
                cfg = row.cfg
                if not cfg.enabled:
                    continue
                trigger = cfg.trigger
                if not isinstance(trigger, MidiMessageTrigger):
                    continue
                if trigger.patch_id and (trigger.patch_id != event.patch_id):
                    continue
                if trigger.type != event.type:
                    continue
                if trigger.channel is not None and trigger.channel != event.channel:
                    continue
                if trigger.number is not None and trigger.number != event.number:
                    continue
                if trigger.value is not None and trigger.value != event.value:
                    continue
                # ``[note]`` = ``event.number``, ``[velocity]`` =
                # ``event.value``; both ``None`` for non-note events.
                is_note = event.type in ("note_on", "note_off")
                self._event_plans.append(
                    self._plan_for_row(
                        row,
                        event_value=event.value,
                        event_velocity=event.value if is_note else None,
                        event_note=event.number if is_note else None,
                    )
                )

    def _handle_fader_change(
        self,
        fader_index: int,
        value: float,
    ) -> None:
        """Enqueue a plan for every :class:`FaderOnChangeTrigger` row
        bound to ``fader_index``. Runs on the rtmidi callback thread.

        Throttle: per-row time-based – the last-fire timestamp is
        compared against ``1.0 / trigger.rate_hz`` so the row can't emit
        faster than its rate even when the bus fires on every distinct
        value. ``rate_hz`` is snapped to the valid set at save time, so
        the division can't hit ``0`` or negative.

        ``value`` is read back through ``fader_resolver`` at render time
        rather than carried on the plan, so the latest value wins when
        events arrive faster than ``1/rate_hz`` apart – desirable for a
        sweeping fader (final position, not the path).
        """
        if self._stop.is_set():
            return
        # Read time outside the lock; the per-row ``last`` fetch under the
        # lock serialises the throttle decision.
        now = time.monotonic()
        with self._lock:
            for row in self._rows.values():
                cfg = row.cfg
                if not cfg.enabled:
                    continue
                trigger = cfg.trigger
                if not isinstance(trigger, FaderOnChangeTrigger):
                    continue
                # Marker-sourced rows are driven by the marker channel;
                # skip them here so a marker trigger (whose unused
                # ``fader`` defaults to 1) doesn't also fire on indexed
                # fader 1.
                if trigger.marker_id:
                    continue
                if trigger.fader != fader_index:
                    continue
                last = self._last_event_fire_ts.get(cfg.id, 0.0)
                if now - last < 1.0 / trigger.rate_hz:
                    continue
                self._last_event_fire_ts[cfg.id] = now
                self._event_plans.append(self._plan_for_row(row))

    def _handle_marker_fader_change(
        self,
        marker_id: int,
        value: float,
    ) -> None:
        """Enqueue a plan for every :class:`FaderOnChangeTrigger` row
        whose ``marker_id`` matches the marker whose gamepad fader just
        moved. Runs on the gamepad poll thread.

        Same per-row ``rate_hz`` throttle and value-read-back contract as
        :meth:`_handle_fader_change` (via ``marker_fader_resolver``).
        ``marker_id`` is always >= 1 here, so indexed triggers
        (``marker_id == 0``) never match – the two channels stay
        partitioned.
        """
        if self._stop.is_set():
            return
        now = time.monotonic()
        with self._lock:
            for row in self._rows.values():
                cfg = row.cfg
                if not cfg.enabled:
                    continue
                trigger = cfg.trigger
                if not isinstance(trigger, FaderOnChangeTrigger):
                    continue
                if trigger.marker_id != marker_id:
                    continue
                last = self._last_event_fire_ts.get(cfg.id, 0.0)
                if now - last < 1.0 / trigger.rate_hz:
                    continue
                self._last_event_fire_ts[cfg.id] = now
                self._event_plans.append(self._plan_for_row(row))

    def _explicit_marker_resolver(
        self,
        marker_id: int,
    ) -> tuple[float, float, float] | None:
        """Bridge from :class:`RenderContext.marker_resolver` to the
        :class:`MarkerProvider`. Returns the position, or ``None`` if the
        marker isn't registered (renderer turns ``None`` into a
        :class:`RenderError`)."""
        marker = self._marker_provider(marker_id)
        if marker is None:
            return None
        try:
            return marker.pos
        except Exception:  # pragma: no cover - defensive
            return None

    def _explicit_fader_resolver(self, fader_index: int) -> float | None:
        """Bridge from :class:`RenderContext.fader_resolver` to the
        :class:`FaderProvider`. Returns the fader's current 0..1 value,
        or ``None`` when the resolver isn't wired, the index isn't
        registered, or the provider raises (renderer turns ``None`` into a
        :class:`RenderError`). The provider runs a live subsystem on the
        scheduler thread; catching here keeps a provider hiccup to a
        single-row skip rather than aborting the plan render – mirrors
        :meth:`_explicit_marker_resolver`."""
        if self._fader_provider is None:
            return None
        try:
            return self._fader_provider(fader_index)
        except Exception:
            return None

    def _explicit_marker_fader_resolver(
        self,
        marker_id: int,
    ) -> float | None:
        """Bridge from :class:`RenderContext.marker_fader_resolver` to the
        :class:`MarkerFaderProvider` (the ``[markerfader]`` placeholder).
        Returns the marker's current 0..1 fader value, or ``None`` when
        the provider isn't wired, the marker has no provisioned fader, or
        the provider raises (renderer turns ``None`` into a
        :class:`RenderError`). Guarded like :meth:`_explicit_marker_resolver`
        so a provider hiccup is a single-row skip, not a render abort."""
        if self._marker_fader_provider is None:
            return None
        try:
            return self._marker_fader_provider(marker_id)
        except Exception:
            return None

    def _explicit_controller_marker_resolver(
        self,
        controller_idx: int,
    ) -> int | None:
        """Bridge from :class:`RenderContext.controller_marker_resolver` to
        the :class:`ControllerMarkerProvider` (the ``:cN`` reference).
        Returns the marker id controller ``controller_idx`` currently
        drives, or ``None`` when the provider isn't wired, the controller
        drives no marker, or the provider raises (renderer turns ``None``
        into a ring-buffer skip). The wired provider reads the gamepad
        subsystem + ``_controlled_ids`` on the scheduler thread while they
        are rebound on hot-reload; guarding here keeps the defensive
        contract at the resolver boundary, not on every provider author."""
        if self._controller_marker_provider is None:
            return None
        try:
            return self._controller_marker_provider(controller_idx)
        except Exception:
            return None

    def _render_plan_pure(self, plan: _TickPlan) -> _RenderResult:
        """Run a tick plan through the renderer without sending or
        logging. Returns a :class:`_RenderResult` carrying either the
        rendered ``(address, args)`` or the ``error`` explaining a skip.
        Both :meth:`_fire_plan` and the diagnostic preview drive off this
        helper, so skip-on-no-data semantics (default marker missing,
        render error, empty address) stay identical.

        The default-marker lookup is gated on ``needs_default_marker``: a
        row of all literals + explicit-marker refs has no dependency on
        the default marker and must dispatch even when none is
        registered. ``plan.marker_id`` is :class:`int | None`; a row that
        needs the default marker but has none picked (``None``) skips with
        ``"no default marker configured"`` rather than calling the
        provider with ``None``.

        The lookup still runs whenever ``marker_id`` is set even if the
        templates don't reference it, because the on-change gate uses the
        default marker as its signal source.
        """
        needs_default = plan.needs_default_marker
        pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
        if plan.marker_id is not None:
            marker = self._marker_provider(plan.marker_id)
            if marker is None:
                if needs_default:
                    # Default-marker miss for a row that uses the
                    # placeholder – skip.
                    return _RenderResult(
                        address="",
                        args=(),
                        skipped=True,
                        error=(f"default marker {plan.marker_id} not registered"),
                    )
                # Marker is the gate signal but the templates don't use
                # it. Render proceeds; the gate is a no-op (a missing
                # marker means "no signal to compare").
            else:
                try:
                    pos = marker.pos
                except Exception:  # pragma: no cover - defensive
                    logger.debug(
                        "OSC transmitter: marker %d position read raised",
                        plan.marker_id,
                    )
                    if needs_default:
                        return _RenderResult(
                            address="",
                            args=(),
                            skipped=True,
                            error=f"marker {plan.marker_id} position read failed",
                        )
        elif needs_default:
            # ``marker_id is None`` and templates use the default-marker
            # placeholder – no marker to render with.
            return _RenderResult(
                address="",
                args=(),
                skipped=True,
                error="no default marker configured",
            )
        grid_w, grid_d, grid_h, z_offset = self._grid_provider()
        rc = RenderContext(
            pos_m=pos,
            grid_w=grid_w,
            grid_d=grid_d,
            grid_h=grid_h,
            z_offset=z_offset,
            marker_id=plan.marker_id,
            marker_resolver=self._explicit_marker_resolver,
            # ``fader_resolver`` is always wired (returns ``None`` with no
            # ``fader_provider``); a ``[fader:N]`` / ``[fader]`` slot
            # resolving to ``None`` raises :class:`RenderError`, caught
            # below as a ring-buffer skip. Event slots are ``None`` for
            # non-event plans, so a ``[value]`` / ``[velocity]`` /
            # ``[note]`` reference there raises the same way.
            fader_resolver=self._explicit_fader_resolver,
            # ``[markerfader]`` resolves against ``plan.marker_id``;
            # always wired, ``None`` (→ RenderError) when no provider or
            # the marker has no fader.
            marker_fader_resolver=self._explicit_marker_fader_resolver,
            # ``:cN`` controller reference; always wired, ``None`` (→
            # RenderError → ring-buffer skip) when no provider or the
            # controller drives no marker.
            controller_marker_resolver=self._explicit_controller_marker_resolver,
            default_fader=plan.default_fader,
            event_value=plan.event_value,
            event_velocity=plan.event_velocity,
            event_note=plan.event_note,
        )
        try:
            address = render(plan.compiled_address, rc)
            # ``send_message`` infers the OSC typetag from the Python
            # type (int → 'i', float → 'f', str → 's'); coerce to the
            # type matching ``osc_arg_for``'s typetag so pythonosc emits
            # the promised wire type.
            args: list[Any] = []
            # ``const_args`` holds the pre-typed value for args that
            # render to a compile-time constant, skipping the per-fire
            # ``osc_arg_for`` parse. Same length as ``compiled_args``; a
            # ``None`` entry means render per-fire.
            for arg_ct, const in zip(plan.compiled_args, plan.const_args, strict=False):
                if const is not None:
                    args.append(const)
                    continue
                typetag, value = osc_arg_for(arg_ct, rc)
                if typetag == "i":
                    args.append(int(value))
                elif typetag == "f":
                    args.append(float(value))
                else:
                    args.append(str(value))
        except RenderError as exc:
            # Renderer-side skip-on-missing-placeholder. Sources here:
            # - ``[x:N]`` whose marker id isn't registered (or
            #   resolver unwired) – ``placeholder = "x:70"``, no
            #   hint.
            # - ``[z.frac]`` / ``[z.frac.inv]`` with ``max_height`` unset –
            #   ``placeholder = "z.frac"`` / ``"z.frac.inv"``, ``hint`` set.
            # The renderer's other raise (default-marker slot with
            # ``ctx.marker_id is None``) can't reach here because the gate
            # above catches it; this branch only guards callers that build
            # a :class:`RenderContext` outside the manager.
            #
            # Append any hint so the ring buffer shows the actionable
            # message, not just the placeholder name.
            error_msg = exc.placeholder
            if exc.hint:
                error_msg = f"{exc.placeholder}: {exc.hint}"
            return _RenderResult(
                address="",
                args=(),
                skipped=True,
                error=error_msg,
            )
        if not address:
            # ``OscService.send`` silently drops empty addresses, so
            # without this pre-check the ring buffer would show ``"sent"``
            # for a packet that never left the process. Reachable when
            # ``cfg.address`` is blank with no built-in template picked.
            return _RenderResult(
                address="",
                args=(),
                skipped=True,
                error="empty address",
            )
        return _RenderResult(
            address=address,
            args=tuple(args),
            skipped=False,
            error="",
            # Surface the position so ``_fire_plan`` can apply the
            # on-change gate without re-fetching. Populated whenever
            # ``marker_id`` is set, even for rows whose message doesn't
            # reference it; ``None`` only when no default marker is set.
            default_marker_pos=pos if plan.marker_id is not None else None,
        )

    def _fire_plan(self, plan: _TickPlan, *, is_test: bool = False) -> None:
        """Render + send one plan. ``is_test=True`` (the "Send test
        packet" path) bypasses the on-change cache, read and write, so a
        manual probe can't suppress a subsequent live tick or test
        packet.
        """
        result = self._render_plan_pure(plan)
        if result.skipped:
            plan.ring_buffer.record_skipped(error=result.error)
            return
        # On-change gate: skip the send when the row's default marker
        # hasn't moved by ``min_change_m`` along any axis since the last
        # successful send. Per-axis (not 3-D Euclidean) comparison –
        # cheaper and matches the "minimum change in metres" model. The
        # gate watches only the default marker, not message contents.
        #
        # Skip preconditions: Stream trigger with mode == "on_change",
        # AND a live tick (``is_test=False``), AND a registered default
        # marker (``default_marker_pos`` populated).
        if not is_test and plan.stream_mode == "on_change" and result.default_marker_pos is not None:
            # Cache read under the manager lock serialises against
            # ``restart()``; released before ``service.send()`` keeps
            # network I/O off the critical path.
            with self._lock:
                last = self._last_sent_pos.get(plan.row_id)
            if last is not None:
                cur = result.default_marker_pos
                dx = abs(cur[0] - last[0])
                dy = abs(cur[1] - last[1])
                dz = abs(cur[2] - last[2])
                delta = max(dx, dy, dz)
                # With ``min_change_m = 0`` the strict ``delta <
                # min_change_m`` test is never true (deltas are
                # absolute), so special-case zero to drop exact
                # duplicates while keeping ``send when delta >=
                # min_change_m`` for non-zero thresholds.
                if delta == 0 if plan.stream_min_change_m == 0 else delta < plan.stream_min_change_m:
                    plan.ring_buffer.record_skipped(
                        error=(
                            f"unchanged (Δ < {plan.stream_min_change_m:g} m)"
                            if plan.stream_min_change_m > 0
                            else "unchanged (bit-exact)"
                        ),
                    )
                    return
        self._service.send(
            result.address,
            list(result.args),
            host=plan.host,
            port=plan.port,
            protocol=plan.protocol,
            framing=plan.framing,
        )
        # Update the last-sent cache only after the send went out, so a
        # skipped send can't prime it. Locked so a concurrent
        # ``restart()`` can't drop the entry between check and write. The
        # *identity* check (not just ``row_id in self._rows``) refuses to
        # resurrect entries for rows ``restart()`` removed during the
        # network send AND won't prime a different row re-created under the
        # same id mid-send – which would suppress that new row's genuine
        # first send as "unchanged". ``restart()`` reuses the row object
        # for a surviving id, so steady-state cache continuity is kept.
        if not is_test and result.default_marker_pos is not None:
            with self._lock:
                if self._rows.get(plan.row_id) is plan.source_row:
                    self._last_sent_pos[plan.row_id] = result.default_marker_pos
        plan.ring_buffer.record_sent(result.address, list(result.args))
