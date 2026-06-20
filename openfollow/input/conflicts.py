# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Central registry of input-binding ownership.

A small, in-memory registry that names *who* owns *which* keyboard
keys, controller buttons, and axes. Subsystems register their bindings
under a stable owner string; other subsystems consult the registry
before accepting a new binding.

The :data:`openfollow.configuration.RESERVED_MOVEMENT_KEYS` set and its
uses in ``ControllerConfig.__post_init__`` and
``web/validation._validate_keybinding`` are *not* rerouted here, because
those run at config-load and per-keystroke validation time without a
runtime services context.

The registry is owned by :class:`openfollow.services.AppRuntimeServices`
so it's available to every init / hot-reload path; consumers register
their bindings on init and re-register on hot-reload. The registry
pre-registers the movement keys under owner ``"system:movement"`` so
consumers see them as already-claimed.

Design notes:

- **Generic over input kinds.** The same registry stores key, button,
  and axis claims so a future "axis already used by gamepad config"
  check has the same shape as today's "key already used by movement"
  check. The :class:`InputKind` literal documents the supported set.
- **Multi-owner per binding.** Two OSC bindings on the same key are
  not in conflict – multiple OSC bindings on the same input fire
  independently. The registry stores a *set* of owners per binding;
  ``conflicts_for(..., owner=X)`` excludes ``X`` itself so a row's own
  re-registration on hot-reload doesn't create a self-conflict. The
  web UI's "no inter-OSC-row conflict" rule is enforced by the OSC
  subsystem all registering under the *same* owner string (e.g.
  ``"osc:hotkey"``); keys it claims are not blocked from being claimed
  again by the same owner.
- **No persistence.** The registry is rebuilt from config on every
  startup. Hot-reload uses :meth:`ConflictRegistry.replace_owner_bindings`
  to swap an owner's binding set in one atomic step – naive
  ``unregister_owner`` followed by per-binding ``register`` would
  expose a transient unregistered state to a concurrent reader (e.g.
  a web-validation request landing mid-reload).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

InputKind = Literal[
    "key",
    "controller_button",
    "controller_axis",
    "mouse_axis",
    # MIDI message identity. ``MidiMessageTrigger`` rows register under
    # :data:`OSC_MIDI_OWNER` so blur validation can refuse a binding
    # that's already claimed elsewhere. The identifier shape is a
    # four-part composite of ``(alias, type, channel, number)`` – see
    # :func:`midi_identifier` for the canonical encoding.
    "midi",
]


# Owner string for the static movement-key reservation. The constant
# lives here rather than scattered across consumers so a registry
# dump (future diagnostics surface) shows a consistent owner name
# regardless of which subsystem populated it.
SYSTEM_MOVEMENT_OWNER = "system:movement"

# Owner string for OSC bindings whose trigger is a
# :class:`MidiMessageTrigger`. Sibling to ``"osc:hotkey"`` and
# ``"osc:controller_button"`` used by
# :meth:`AppRuntimeServices._sync_osc_binding_conflicts`. The constant
# lives here so consumers import a single source of truth instead of
# comparing string literals across modules.
OSC_MIDI_OWNER = "osc:midi"


def midi_identifier(
    patch_id: int,
    type_: str,
    channel: int,
    number: int | None,
) -> str:
    """Canonical string-encoding of a MIDI message identity.

    The :class:`InputBinding.identifier` field is a string, but a MIDI
    message is naturally a four-part tuple – patch id, message type, channel,
    and (for note / CC events) note or CC number. The encoding's contract is
    **bijective uniqueness**: distinct input tuples always produce distinct
    strings. Since ``patch_id`` is a non-negative integer it can't contain a
    ``:`` separator, so no escaping is needed (unlike the old alias-string
    key).

    Wildcards: ``patch_id`` of ``0`` = "any patch", ``channel`` of ``0`` =
    "any channel" (:class:`MidiMessageTrigger`), ``number`` of ``None`` = a
    wildcard / a type without a per-message number (program_change /
    channel_pressure), encoded as the literal ``"any"`` so a missing number
    can't collide with a real ``0``.

    Conflict semantics: two MIDI bindings with the same identifier string
    conflict. Wildcards are matched by *encoded equality*, not semantic
    overlap – ``"1:control_change:0:7"`` (any channel) and
    ``"1:control_change:1:7"`` (channel 1) do NOT conflict in the registry;
    the runtime dispatcher matches incoming messages against both.
    """
    number_part = "any" if number is None else str(number)
    return f"{patch_id}:{type_}:{channel}:{number_part}"


@dataclass(frozen=True)
class InputBinding:
    """One claimed input. ``identifier`` is the kind-specific name –
    ``"w"`` for a key, ``"DPAD_UP"`` for a controller button, etc."""

    kind: InputKind
    identifier: str


class ConflictRegistry:
    """In-memory registry of input-binding ownership.

    Thread-safe; modifications and reads are short, so the API uses
    one coarse lock rather than per-binding locks. Expected size is
    O(tens) of entries (movement keys + a handful of OSC HotkeyTrigger
    rows + a handful of MIDI bindings), so traversal cost is bounded.

    The registry treats two registrations of the same binding under
    the *same* owner as idempotent – :meth:`register` is safe to call
    on every hot-reload pass. Different owners on the same binding
    are conflicts, surfaced via :meth:`conflicts_for`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners_by_binding: dict[InputBinding, set[str]] = {}
        self._bindings_by_owner: dict[str, set[InputBinding]] = {}

    def register(self, owner: str, binding: InputBinding) -> None:
        """Claim ``binding`` for ``owner``. Idempotent under the same
        owner – repeat calls are no-ops."""
        with self._lock:
            self._owners_by_binding.setdefault(binding, set()).add(owner)
            self._bindings_by_owner.setdefault(owner, set()).add(binding)

    def replace_owner_bindings(
        self,
        owner: str,
        bindings: Iterable[InputBinding],
    ) -> None:
        """Atomically swap ``owner``'s registered bindings for the
        provided iterable. The whole operation runs under one lock
        acquisition so a concurrent reader never sees a transient
        "unregistered" state – the relevant case is hot-reload: an
        OSC HotkeyTrigger row's key is briefly unclaimed if we did
        ``unregister_owner`` + per-binding ``register`` separately,
        which would let a parallel ``conflicts_for`` call (e.g. from
        the web validation path) see the binding as available when
        in fact the owner is just re-registering.

        Idempotent: calling with the current set is a no-op.
        Removes claims that aren't in the new set; adds claims that
        weren't previously present.
        """
        new_bindings = set(bindings)
        with self._lock:
            old_bindings = self._bindings_by_owner.get(owner, set())
            # Drop ``owner`` from any binding that's no longer in the
            # new set; clean up empty entries so ``conflicts_for``
            # stays accurate.
            for binding in old_bindings - new_bindings:
                owners = self._owners_by_binding.get(binding)
                # pragma: no cover – same defensive lockstep guard as
                # ``unregister_owner``: ``register`` and the bindings/
                # owners maps are kept in sync, so this fallback is
                # only reachable if a future change splits that.
                if owners is None:  # pragma: no cover
                    continue
                owners.discard(owner)
                if not owners:
                    self._owners_by_binding.pop(binding, None)
            # Add ``owner`` to any binding that wasn't previously
            # claimed by this owner.
            for binding in new_bindings - old_bindings:
                self._owners_by_binding.setdefault(binding, set()).add(owner)
            if new_bindings:
                self._bindings_by_owner[owner] = new_bindings
            else:
                # No bindings left – drop the owner entry entirely so
                # ``all_owners`` doesn't list a no-op subsystem.
                self._bindings_by_owner.pop(owner, None)

    def unregister_owner(self, owner: str) -> None:
        """Drop every binding claimed by ``owner``. Used on hot-reload
        before re-registering the new binding set, and on subsystem
        shutdown to release claims so a future re-init doesn't see
        stale ownership."""
        with self._lock:
            bindings = self._bindings_by_owner.pop(owner, set())
            for binding in bindings:
                owners = self._owners_by_binding.get(binding)
                # pragma: no cover – defensive: ``register`` and
                # ``unregister_owner`` keep the two maps in lockstep
                # under the same lock, so an entry in
                # ``_bindings_by_owner`` always has a matching entry
                # in ``_owners_by_binding``. The fallback only fires
                # if a future change splits that invariant.
                if owners is None:  # pragma: no cover
                    continue
                owners.discard(owner)
                if not owners:
                    # Last owner gone – drop the empty entry so
                    # ``conflicts_for`` doesn't return an empty set
                    # for a binding nobody claims.
                    self._owners_by_binding.pop(binding, None)

    def conflicts_for(
        self,
        binding: InputBinding,
        *,
        owner: str,
    ) -> list[str]:
        """Return the names of owners (other than ``owner`` itself)
        already claiming ``binding``. Empty list means the binding is
        free for ``owner`` to take.

        Sorted alphabetically so error messages and UI surfaces have
        a stable ordering across calls – multi-conflict scenarios
        (e.g. a key claimed by both ``system:movement`` and
        ``osc:hotkey``) shouldn't reorder the rendered list between
        page loads.
        """
        with self._lock:
            owners = self._owners_by_binding.get(binding, set())
            return sorted(o for o in owners if o != owner)

    def is_available(
        self,
        binding: InputBinding,
        *,
        owner: str,
    ) -> bool:
        """``True`` if ``binding`` has no conflicting claims for
        ``owner``. Sugar over :meth:`conflicts_for`."""
        return not self.conflicts_for(binding, owner=owner)

    def bindings_for(self, owner: str) -> list[InputBinding]:
        """Defensive snapshot of every binding claimed by ``owner``.
        Test surface + future diagnostics endpoint."""
        with self._lock:
            return sorted(
                self._bindings_by_owner.get(owner, set()),
                key=lambda b: (b.kind, b.identifier),
            )

    def all_owners(self) -> list[str]:
        """Defensive snapshot of every owner that currently has at
        least one registered binding."""
        with self._lock:
            return sorted(self._bindings_by_owner.keys())


def default_registry(
    reserved_movement_keys: Iterable[str],
) -> ConflictRegistry:
    """Build a fresh registry pre-populated with the system's
    movement-key reservations under :data:`SYSTEM_MOVEMENT_OWNER`.

    The caller passes :data:`openfollow.configuration.RESERVED_MOVEMENT_KEYS`
    (a ``frozenset``) but any iterable of strings works – keeps the
    factory friction-free for callers that have a ``set`` / ``list``
    / generator of keys lying around. The registry imports from
    ``configuration`` are pushed to the call site so this module
    stays dependency-free of the rest of the project and stays cheap
    to import in tests and from any subsystem.
    """
    registry = ConflictRegistry()
    for key in reserved_movement_keys:
        registry.register(
            SYSTEM_MOVEMENT_OWNER,
            InputBinding(kind="key", identifier=key),
        )
    return registry
