# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""USB MIDI device manager using mido / python-rtmidi.

Owns the mido lifecycle: discovers connected devices, opens listener ports for each
aliased device, fans incoming MIDI messages to subscribers, and supports MIDI Learn
capture from the web UI.

Key design:

- **Patch ID as foreign key:** The system names devices by stable integer patch id,
  never by port name or alias. ``apply_config`` maps :class:`~openfollow.configuration.MidiPatch`
  entries to open mido ports.

- **Identification (best-effort):** mido exposes only port-name strings; USB serial
  enumeration needs platform-specific code (pyudev / IOKit / setupapi). Currently
  returns ``serial=None``; the field exists for future serial-based matching without
  schema migration.

- **Status signaling:** When a patch has no matching device, ``apply_config`` writes
  to ``status_flags["midi_patch_missing"]``. Set to ``None`` when all patches resolve.

- **Defensive fail-soft:** If mido/rtmidi import fails at runtime (broken install,
  missing headers), ``available`` is ``False``, ``discover`` returns ``[]``,
  ``apply_config`` opens nothing. The import error lands in ``status_flags["midi_unavailable"]``
  only if at least one patch is configured. The rest of the app continues regardless.

- **Subscriber threading:** ``subscribe`` returns an idempotent unsubscribe callable.
  Subscribers run on rtmidi's listener thread and must not block; exceptions are
  caught and logged.

- **MIDI Learn:** ``arm_capture`` blocks the caller on a one-shot event until the
  next message or timeout. Single-caller (web UI serialises).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from openfollow.configuration import MidiPatch

logger = logging.getLogger(__name__)


# Eager import behind a try/except so a host without rtmidi headers
# can still boot the rest of the app. Tests monkeypatch this module
# attribute to inject a fake mido implementation; the
# ``status_flags["midi_unavailable"]`` slot surfaces the original
# import error to the operator via the future status badge.
try:
    import mido as _mido

    _MIDO_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover – tested via monkeypatch
    _mido = None
    _MIDO_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


# OSC-side message types we round-trip. Pitch-bend, sysex, MIDI clock, etc.
# are dropped because they aren't modeled as MIDI triggers.
_MIDI_EVENT_TYPES: tuple[str, ...] = (
    "note_on",
    "note_off",
    "control_change",
    "program_change",
    "key_pressure",
    "channel_pressure",
)


# A trailing ALSA SEQ client:port address, e.g. the `` 16:0`` in
# ``"nanoKONTROL2:nanoKONTROL2 _ CTRL 16:0"``. The numbers are assigned at
# device-registration time and shift when USB enumeration order or boot
# timing changes, so they must not be part of a device's saved identity.
_ALSA_ADDR_SUFFIX = re.compile(r"\s+\d+:\d+$")


def _normalize_port_name(name: str) -> str:
    """Strip the volatile ALSA ``client:port`` suffix from a mido port name.

    On Linux, mido's ALSA SEQ backend names ports ``"<client>:<port> N:M"``
    where the trailing `` N:M`` is the runtime-assigned address – it changes
    across restarts / replug, which made a saved MIDI patch stop matching its
    device after a reboot (users reported the controller "not recognised as
    the same hardware"). The stable identity is everything before that suffix.

    Names without the suffix – macOS / Windows port names, or a port that
    never carried one – pass through unchanged, so this is a no-op off Linux.
    """
    return _ALSA_ADDR_SUFFIX.sub("", name)


@dataclass(frozen=True)
class DiscoveredDevice:
    """A MIDI input device the OS currently exposes.

    ``port_name`` / ``product`` are normalized identity (volatile ALSA ``client:port``
    suffix stripped by :func:`_normalize_port_name`), so a device keeps the same
    identity across restarts / replug. ``raw_port_name`` is the exact OS name mido
    needs to open the port (carries volatile suffix, never use as identity key).
    ``identifier`` is the stable match key – serial wins when present, else the
    normalized (port_name, product) composite.
    """

    serial: str | None
    port_name: str
    product: str
    raw_port_name: str = ""

    @property
    def identifier(self) -> str:
        if self.serial:
            return f"serial:{self.serial}"
        return f"port:{self.port_name}|{self.product}"

    @property
    def open_name(self) -> str:
        """The exact name to hand mido's ``open_input`` – the raw OS name
        when known, else the (normalized) ``port_name`` as a fallback for
        directly-constructed devices that didn't capture a raw name."""
        return self.raw_port_name or self.port_name


@dataclass(frozen=True)
class MidiEvent:
    """One MIDI message routed through the subsystem.

    ``channel`` is 1–16 (mido exposes 0–15 internally; converted at
    the conversion boundary so operator-facing comparisons match the
    industry convention). ``number`` is the note number for note
    events, the CC number for control_change, and ``None`` for
    program_change / channel_pressure where the protocol carries no
    "what" – only a "how much".
    """

    type: str  # one of _MIDI_EVENT_TYPES
    channel: int
    number: int | None
    value: int
    patch_id: int  # MIDI patch the event's port belongs to
    timestamp: float

    def as_dict(self) -> dict[str, Any]:
        """Return the five wire fields shared by MIDI-Learn and diagnostics.

        Excludes the monotonic ``timestamp`` – each consumer wraps with
        its own ``status`` / ``age_s``."""
        return {
            "patch_id": self.patch_id,
            "type": self.type,
            "channel": self.channel,
            "number": self.number,
            "value": self.value,
        }


@dataclass
class _OpenPort:
    """Internal: one open mido InputPort plus its source port name."""

    port: Any  # mido.ports.BaseInput; ``Any`` because mido has no stubs
    port_name: str


# A status-flag value the top-right HUD badge renders: a message string
# (styled as an "error"), an explicit ``(severity, message)`` tuple to pick
# "error" (red) vs "info" (green), or ``None`` to clear the condition. The
# dict is shared with :class:`AppRuntimeServices`; MidiSubsystem only ever
# writes the string / None forms, but it must accept the shared wider type.
StatusFlagValue = str | tuple[str, str] | None


# How often :meth:`MidiSubsystem.poll_hotplug` is allowed to enumerate the
# connected MIDI ports. The enumeration is cheap but not free, so the per-tick
# caller is throttled to this cadence – 2 s is responsive enough for an
# unplug / replug badge without hitting ALSA on every animation frame.
_HOTPLUG_POLL_INTERVAL_S = 2.0

# Capacity of the always-on recent-event ring; bounded to prevent unbounded growth.
# Sized to match the OSC transmitter's ring capacity.
_EVENT_RING_CAPACITY = 100


class MidiSubsystem:
    """USB MIDI device manager.

    See module docstring for the design contract. Public surface:

    - :meth:`available`        – has mido / rtmidi loaded?
    - :meth:`discover`         – list currently-connected devices.
    - :meth:`apply_config`     – open listeners for each alias; idempotent
                                  and hot-reload-safe.
    - :meth:`subscribe`        – register an event callback.
    - :meth:`arm_capture`      – block for the next event (MIDI Learn).
    - :meth:`shutdown`         – close every open port.

    The ``status_flags`` dict is borrowed from
    :class:`AppRuntimeServices`; the subsystem writes
    ``midi_unavailable`` and ``midi_patch_missing`` slots into it so
    one badge surface covers every error source uniformly.
    """

    def __init__(
        self,
        status_flags: dict[str, StatusFlagValue] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._open_ports: dict[int, _OpenPort] = {}
        self._subscribers: list[Callable[[MidiEvent], None]] = []
        self._capture_event: threading.Event | None = None
        self._capture_slot: MidiEvent | None = None
        # Always-on recent-event ring; distinct from the learn-mode capture slot.
        self._event_ring: deque[MidiEvent] = deque(maxlen=_EVENT_RING_CAPACITY)
        # Hotplug tracking. ``apply_config`` records the patch set and the
        # connected port names it last saw; ``poll_hotplug`` re-applies when
        # that port set changes, so the ``midi_patch_missing`` badge (and the
        # open listener ports) track live hardware without an explicit config
        # save. Mirrors the gamepad handler's per-tick hotplug detection.
        self._patches: list[MidiPatch] = []
        self._last_input_names: frozenset[str] | None = None
        self._last_hotplug_check: float = 0.0
        self._status_flags: dict[str, StatusFlagValue] = status_flags if status_flags is not None else {}
        # Initialise the badge slot clear regardless of backend state. The
        # "MIDI backend unavailable" message is surfaced from ``apply_config``
        # only once the configured patch set is known: an install that never
        # uses MIDI (no patches) must not carry a red status badge for a
        # feature it doesn't touch. ``available`` reads the import state directly, so
        # deferring the badge here doesn't change what callers see.
        self._status_flags["midi_unavailable"] = None

    @property
    def available(self) -> bool:
        """``True`` when mido + rtmidi are usable.

        Reflects both import success and runtime backend health. Consults
        the ``midi_unavailable`` status flag set by :meth:`discover`.
        """
        if _mido is None:
            return False
        return self._status_flags.get("midi_unavailable") is None

    def discover(self) -> list[DiscoveredDevice]:
        """List currently-connected USB MIDI input devices.

        Returns an empty list when mido is unavailable. Runtime backend
        failures are caught and recorded into ``midi_unavailable``.
        On successful enumeration, the flag is cleared so transient
        errors don't permanently disable the subsystem.
        """
        if _mido is None:
            return []
        try:
            names = _mido.get_input_names()
        except Exception as exc:
            logger.exception("MIDI device discovery failed")
            self._status_flags["midi_unavailable"] = f"MIDI backend error: {type(exc).__name__}: {exc}"
            return []
        # Successful enumeration – drop the unavailable flag if a prior
        # transient failure left it set so the subsystem can recover
        # without an app restart. The only writer that reaches this slot is
        # the ``except`` branch just above (a runtime backend error); the
        # ``import mido`` failure path is surfaced from ``apply_config`` and
        # can't reach here because ``_mido is None`` short-circuits at the top.
        if self._status_flags.get("midi_unavailable") is not None:
            self._status_flags["midi_unavailable"] = None
        # Normalize the identity (port_name / product) so a device matches its
        # saved patch across restarts even when the ALSA client:port number
        # shifts; keep the raw name for the actual ``open_input`` call.
        return [
            DiscoveredDevice(
                serial=None,
                port_name=_normalize_port_name(name),
                product=_normalize_port_name(name),
                raw_port_name=name,
            )
            for name in names
        ]

    def apply_config(self, patches: Sequence[MidiPatch]) -> None:
        """Open / close listener ports to match the patch set.

        Hot-reload-safe. Reports missing devices via the ``midi_patch_missing``
        status flag. Backend unavailable surfaces ``midi_unavailable`` only when
        patches are configured; unused-MIDI hosts carry no warning.
        """
        # Remember the patch set so ``poll_hotplug`` can re-apply it when the
        # connected device set changes (unplug / replug) without the caller
        # re-passing it. Stored before the backend-availability guard so a
        # later backend recovery still re-applies the right patches.
        self._patches = list(patches)
        if _mido is None:
            # Backend import absent (defensive path now that mido/rtmidi are
            # base deps – a broken install, or a ``git pull`` without
            # ``poetry install``). Surface "unavailable" only when the operator
            # configured MIDI (any patch row signals intent to use it); else
            # stay silent so an unused-MIDI host carries no badge. No ports can
            # be open without the backend, so there's nothing to close.
            # ``midi_patch_missing`` stays clear so the badge shows one cause.
            self._status_flags["midi_unavailable"] = (
                f"MIDI backend unavailable: {_MIDO_IMPORT_ERROR}" if self._patches else None
            )
            self._status_flags["midi_patch_missing"] = None
            return
        # Ports to close are staged under the lock and closed after releasing
        # it: rtmidi's close blocks on the callback thread, which needs the
        # lock (see _detach_port / _shutdown_port).
        staged: list[_OpenPort] = []
        with self._lock:
            # Only patches with a device assigned (something to match on) are
            # candidates for an open port; keyed by the patch's integer id.
            # Iterate the materialized ``self._patches`` (set above) rather than
            # the ``patches`` argument so the input is read exactly once – robust
            # even if a caller ever passes a one-shot iterator.
            new_keys = {p.id: p for p in self._patches if p.id >= 1 and (p.port_name or p.serial)}
            old_keys = set(self._open_ports)

            # Drop patches that disappeared from the config entirely.
            for patch_id in old_keys - set(new_keys):
                self._detach_port(patch_id, staged)

            if not self._patches:
                # No MIDI configured at all → nothing to open and neither MIDI
                # badge applies. Clear both and skip discovery so an install
                # that doesn't use MIDI carries no warning. Any open ports from
                # prior config were already staged for closing above.
                self._status_flags["midi_unavailable"] = None
                self._status_flags["midi_patch_missing"] = None
            else:
                discovered = self.discover()
                # Cache the connected port set so ``poll_hotplug`` can cheaply
                # detect a change (device plugged / unplugged) before paying
                # for a full re-apply.
                self._last_input_names = frozenset(d.raw_port_name for d in discovered)
                if self._status_flags.get("midi_unavailable") is not None:
                    # ``discover()`` recorded a runtime backend failure; skip
                    # the open pass and clear the missing flag so the badge
                    # shows a single root cause.
                    self._status_flags["midi_patch_missing"] = None
                else:
                    missing: list[tuple[int, str]] = []
                    for patch_id, patch in new_keys.items():
                        target = self._match(patch, discovered)
                        if target is None:
                            missing.append((patch_id, patch.label))
                            if patch_id in self._open_ports:
                                # Assigned device was unplugged or renamed.
                                self._detach_port(patch_id, staged)
                            continue
                        # Open / compare on the raw OS name: the normalized
                        # identity already matched in ``_match`` above, but the
                        # actual port to open carries the volatile ALSA suffix.
                        # Comparing the raw name also makes a replug that shifts
                        # the client:port number correctly reopen on the new port.
                        open_name = target.open_name
                        existing = self._open_ports.get(patch_id)
                        if existing is not None and existing.port_name == open_name:
                            continue  # already open on the right port
                        if existing is not None:
                            self._detach_port(patch_id, staged)
                        if not self._open_port(patch_id, open_name):
                            # Open failed – device matched but rtmidi couldn't grab it.
                            missing.append((patch_id, patch.label))

                    self._status_flags["midi_patch_missing"] = (
                        "MIDI patch(es) without a connected device: " + ", ".join(label for _, label in sorted(missing))
                        if missing
                        else None
                    )
        for wrapper in staged:
            self._shutdown_port(wrapper)

    def poll_hotplug(self) -> None:
        """Re-apply the current patch set when the connected MIDI port set
        changes, so an unplug / replug updates the ``midi_patch_missing``
        badge (and opens / closes listener ports) without an explicit config
        save. USB MIDI has no event-based hotplug the way the gamepad layer
        does (pygame ``JOYDEVICEADDED`` / ``REMOVED``), so a periodic caller
        polls instead.

        Cheap by design: throttled to one port enumeration every
        :data:`_HOTPLUG_POLL_INTERVAL_S` seconds, and the heavier
        open / close + flag recompute in :meth:`apply_config` only runs on the
        ticks where the port set actually changed. No-op when the backend is
        unavailable – the ``midi_unavailable`` flag already explains why, and
        :meth:`discover` recovers the subsystem on its own.
        """
        if _mido is None:
            return
        now = time.monotonic()
        if now - self._last_hotplug_check < _HOTPLUG_POLL_INTERVAL_S:
            return
        self._last_hotplug_check = now
        try:
            current = frozenset(_mido.get_input_names())
        except Exception:
            # A backend hiccup here isn't actionable from a hotplug poll; the
            # next discover / apply_config surfaces it via ``midi_unavailable``.
            return
        if current == self._last_input_names:
            return
        # Record the seen set here, not only inside ``apply_config``: that
        # method's no-patches early-exit returns before it caches
        # ``_last_input_names``, so without this a no-patch install would
        # re-apply on every poll forever.
        self._last_input_names = current
        # The connected port set changed – re-apply the stored patches so
        # ports open / close and the missing-patch flag tracks live hardware.
        self.apply_config(self._patches)

    @staticmethod
    def _match(
        patch: MidiPatch,
        discovered: Sequence[DiscoveredDevice],
    ) -> DiscoveredDevice | None:
        """Resolve a configured patch to a connected device.

        Serial wins when both sides have a non-empty value (the only way to
        disambiguate two identical models); otherwise fall through to the
        (port_name, product) composite. Returns ``None`` when nothing matches
        – the subsystem records the patch in ``midi_patch_missing`` rather
        than opening a port for it.
        """
        if patch.serial:
            for d in discovered:
                if d.serial and d.serial == patch.serial:
                    return d
        for d in discovered:
            if d.port_name == patch.port_name and d.product == patch.product:
                return d
        # Last-resort: patch assigned with only ``port_name`` set. Match on
        # port_name alone.
        if patch.port_name:
            for d in discovered:
                if d.port_name == patch.port_name:
                    return d
        return None

    def _open_port(self, patch_id: int, port_name: str) -> bool:
        """Open one mido InputPort and route messages through dispatch.

        Returns ``True`` on success, ``False`` when ``open_input`` raised –
        the caller treats failure as a missing patch so the
        ``midi_patch_missing`` status flag surfaces the silent-drop. The
        opened port runs a private listener thread; messages arrive on
        rtmidi's thread and are forwarded via ``_on_message`` carrying the
        patch id.
        """
        if _mido is None:  # pragma: no cover – guarded at call sites
            return False
        try:
            port = _mido.open_input(port_name)
        except Exception:
            logger.exception(
                "Failed to open MIDI port %s for patch %d",
                port_name,
                patch_id,
            )
            return False
        port.callback = lambda msg, p=patch_id: self._on_message(p, msg)
        self._open_ports[patch_id] = _OpenPort(port=port, port_name=port_name)
        return True

    def _detach_port(self, patch_id: int, staged: list[_OpenPort]) -> None:
        """Stop tracking ``patch_id``'s port under ``self._lock`` and stage the
        wrapper for closing OUTSIDE the lock. Closing here deadlocks: rtmidi's
        ``close`` / ``callback = None`` blocks on ``cancel_callback`` until the
        native callback thread finishes, and that callback (``_on_message``)
        needs ``self._lock`` – which the caller holds."""
        wrapper = self._open_ports.pop(patch_id, None)
        if wrapper is not None:
            staged.append(wrapper)

    @staticmethod
    def _shutdown_port(wrapper: _OpenPort) -> None:
        """Detach the callback and close a staged port. MUST run with
        ``self._lock`` released (see :meth:`_detach_port`)."""
        try:
            wrapper.port.callback = None
            wrapper.port.close()
        except Exception:
            logger.exception("Closing MIDI port %s failed", wrapper.port_name)

    def subscribe(
        self,
        callback: Callable[[MidiEvent], None],
    ) -> Callable[[], None]:
        """Register a subscriber. Returns an idempotent unsubscribe."""
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    def recent_events(self) -> list[MidiEvent]:
        """Snapshot of always-on event ring (oldest first) for diagnostics."""
        with self._lock:
            return list(self._event_ring)

    def connected_port_names(self) -> list[str]:
        """Return normalized names of currently-connected MIDI input ports from cache."""
        with self._lock:
            raw = self._last_input_names
        if not raw:
            return []
        return sorted({n for n in (_normalize_port_name(r) for r in raw) if n})

    def arm_capture(self, timeout_s: float = 10.0) -> MidiEvent | None:
        """Block for the next event from any open port. ``None`` on timeout.

        The capture slot is reset on entry so a stale event from a
        previous arm doesn't leak into the new request. Single-caller
        contract: concurrent arm overwrites the previous arm's
        ``threading.Event`` and the earlier waiter resolves to
        ``None`` (timeout). The web UI serialises Capture so this
        isn't observable in practice.
        """
        evt = threading.Event()
        with self._lock:
            self._capture_event = evt
            self._capture_slot = None
        fired = evt.wait(timeout_s)
        with self._lock:
            captured = self._capture_slot if fired else None
            # Always clear, even on timeout, so the next arm starts clean.
            self._capture_event = None
            self._capture_slot = None
        return captured

    def shutdown(self) -> None:
        """Close every open port and drop all subscribers."""
        staged: list[_OpenPort] = []
        with self._lock:
            for patch_id in list(self._open_ports):
                self._detach_port(patch_id, staged)
            self._subscribers.clear()
            self._capture_event = None
            self._capture_slot = None
        # Close staged ports outside the lock (see _detach_port).
        for wrapper in staged:
            self._shutdown_port(wrapper)

    def _on_message(self, patch_id: int, msg: Any) -> None:
        """rtmidi callback. Convert and fan out to subscribers + capture."""
        event = self._convert(patch_id, msg)
        if event is None:
            return  # message type we don't model (sysex, pitch_bend, …)
        with self._lock:
            # Record every converted event to the ring for diagnostics.
            self._event_ring.append(event)
            subscribers = list(self._subscribers)
            capture_event = self._capture_event
            capture_armed = capture_event is not None and self._capture_slot is None
            if capture_armed:
                self._capture_slot = event
        # Notify capture waiter outside the lock so a slow ``Event.set``
        # path can't block dispatch (Python's Event.set is fast in
        # practice but the listener thread is shared with rtmidi).
        if capture_armed and capture_event is not None:
            capture_event.set()
        for sub in subscribers:
            try:
                sub(event)
            except Exception:
                logger.exception("MIDI subscriber raised")

    @staticmethod
    def _convert(patch_id: int, msg: Any) -> MidiEvent | None:
        """Map a mido Message to a :class:`MidiEvent`.

        Returns ``None`` for message types we don't track (pitch-bend, sysex, etc.).
        Note On with velocity 0 is rewritten as Note Off, as many MIDI controllers
        use this equivalence for key release.
        """
        timestamp = time.monotonic()
        # Channel-bearing messages all carry ``channel`` on the mido
        # side. System messages don't, but they're filtered below
        # anyway – getattr with default 0 keeps the type-checker
        # happy without a runtime branch.
        channel = getattr(msg, "channel", 0) + 1
        type_ = msg.type
        if type_ == "note_on":
            # Velocity-0 Note On is the running-status note-off.
            event_type = "note_off" if msg.velocity == 0 else "note_on"
            return MidiEvent(
                type=event_type,
                channel=channel,
                number=msg.note,
                value=msg.velocity,
                patch_id=patch_id,
                timestamp=timestamp,
            )
        if type_ == "note_off":
            return MidiEvent(
                type="note_off",
                channel=channel,
                number=msg.note,
                value=msg.velocity,
                patch_id=patch_id,
                timestamp=timestamp,
            )
        if type_ == "control_change":
            return MidiEvent(
                type="control_change",
                channel=channel,
                number=msg.control,
                value=msg.value,
                patch_id=patch_id,
                timestamp=timestamp,
            )
        if type_ == "program_change":
            return MidiEvent(
                type="program_change",
                channel=channel,
                number=None,
                value=msg.program,
                patch_id=patch_id,
                timestamp=timestamp,
            )
        if type_ == "polytouch":
            # Polyphonic key pressure per held note.
            return MidiEvent(
                type="key_pressure",
                channel=channel,
                number=msg.note,
                value=msg.value,
                patch_id=patch_id,
                timestamp=timestamp,
            )
        if type_ == "aftertouch":
            # Channel-wide pressure (single value for all held notes).
            return MidiEvent(
                type="channel_pressure",
                channel=channel,
                number=None,
                value=msg.value,
                patch_id=patch_id,
                timestamp=timestamp,
            )
        return None
