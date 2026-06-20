# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the input-binding conflict registry.

Pure data-structure module; no I/O. Concurrency is limited to
lock-based synchronisation, exercised by the explicit thread-pool
tests at the end. Covers register / unregister / replace_owner_bindings
/ conflicts_for / is_available / bindings_for / all_owners + the
``default_registry`` factory's movement-key pre-registration.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from openfollow.configuration import RESERVED_MOVEMENT_KEYS
from openfollow.input.conflicts import (
    OSC_MIDI_OWNER,
    SYSTEM_MOVEMENT_OWNER,
    ConflictRegistry,
    InputBinding,
    default_registry,
    midi_identifier,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# InputBinding dataclass
# ---------------------------------------------------------------------------


class TestInputBinding:
    def test_equality_by_kind_and_identifier(self) -> None:
        a = InputBinding(kind="key", identifier="w")
        b = InputBinding(kind="key", identifier="w")
        assert a == b
        assert hash(a) == hash(b)

    def test_different_kind_same_identifier_not_equal(self) -> None:
        """``"A"`` is both a keyboard letter and a controller button –
        the kind discriminator must keep them separate."""
        key = InputBinding(kind="key", identifier="A")
        button = InputBinding(kind="controller_button", identifier="A")
        assert key != button


# ---------------------------------------------------------------------------
# ConflictRegistry: register / unregister / conflicts_for
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_records_owner_for_binding(self) -> None:
        r = ConflictRegistry()
        r.register("alice", InputBinding(kind="key", identifier="w"))
        assert r.bindings_for("alice") == [
            InputBinding(kind="key", identifier="w"),
        ]

    def test_register_is_idempotent_under_same_owner(self) -> None:
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")
        r.register("alice", binding)
        r.register("alice", binding)
        assert r.bindings_for("alice") == [binding]
        assert r.conflicts_for(binding, owner="alice") == []

    def test_register_two_owners_same_binding_creates_conflict(self) -> None:
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")
        r.register("alice", binding)
        r.register("bob", binding)
        # Bob's perspective: alice already claims it.
        assert r.conflicts_for(binding, owner="bob") == ["alice"]
        # Alice's perspective: bob already claims it.
        assert r.conflicts_for(binding, owner="alice") == ["bob"]

    def test_conflicts_for_excludes_self(self) -> None:
        """The registering owner is never in their own conflict list –
        otherwise every claim would self-conflict."""
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")
        r.register("alice", binding)
        assert r.conflicts_for(binding, owner="alice") == []

    def test_conflicts_for_unknown_binding_is_empty(self) -> None:
        r = ConflictRegistry()
        assert (
            r.conflicts_for(
                InputBinding(kind="key", identifier="z"),
                owner="alice",
            )
            == []
        )

    def test_conflicts_returned_sorted_alphabetically(self) -> None:
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")
        for owner in ["zelda", "alice", "mallory"]:
            r.register(owner, binding)
        assert r.conflicts_for(binding, owner="bob") == [
            "alice",
            "mallory",
            "zelda",
        ]


class TestIsAvailable:
    def test_unclaimed_binding_is_available(self) -> None:
        r = ConflictRegistry()
        assert (
            r.is_available(
                InputBinding(kind="key", identifier="z"),
                owner="alice",
            )
            is True
        )

    def test_self_owned_binding_is_available(self) -> None:
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")
        r.register("alice", binding)
        assert r.is_available(binding, owner="alice") is True

    def test_other_owner_blocks_availability(self) -> None:
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")
        r.register("alice", binding)
        assert r.is_available(binding, owner="bob") is False


class TestUnregisterOwner:
    def test_unregister_drops_owner_bindings(self) -> None:
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")
        r.register("alice", binding)
        r.unregister_owner("alice")
        assert r.bindings_for("alice") == []
        # No owners means the binding entry is fully cleaned up.
        assert r.conflicts_for(binding, owner="bob") == []

    def test_unregister_unknown_owner_is_noop(self) -> None:
        r = ConflictRegistry()
        r.unregister_owner("never-registered")  # must not raise

    def test_unregister_one_owner_leaves_others_alone(self) -> None:
        """When two owners share a binding, unregistering one leaves
        the other's claim intact."""
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")
        r.register("alice", binding)
        r.register("bob", binding)
        r.unregister_owner("alice")
        assert r.bindings_for("alice") == []
        assert r.bindings_for("bob") == [binding]
        # Bob still owns it – a third party seeking the binding
        # still sees Bob as the conflict.
        assert r.conflicts_for(binding, owner="charlie") == ["bob"]


class TestReplaceOwnerBindings:
    """Atomic binding-set swap for hot-reload – no transient
    "unregistered" window observable from a concurrent reader. See
    the rationale at the top of :mod:`openfollow.input.conflicts`.
    """

    def test_replaces_old_set_with_new_set(self) -> None:
        r = ConflictRegistry()
        r.register("alice", InputBinding(kind="key", identifier="a"))
        r.register("alice", InputBinding(kind="key", identifier="b"))
        r.replace_owner_bindings(
            "alice",
            [
                InputBinding(kind="key", identifier="b"),
                InputBinding(kind="key", identifier="c"),
            ],
        )
        # ``a`` is gone, ``b`` survives, ``c`` is new.
        assert r.bindings_for("alice") == [
            InputBinding(kind="key", identifier="b"),
            InputBinding(kind="key", identifier="c"),
        ]

    def test_replace_with_empty_set_drops_owner(self) -> None:
        """An empty new set is a clean unregister – the owner falls
        out of ``all_owners`` so a future diagnostic dump doesn't
        list a no-op subsystem."""
        r = ConflictRegistry()
        r.register("alice", InputBinding(kind="key", identifier="a"))
        r.replace_owner_bindings("alice", [])
        assert r.bindings_for("alice") == []
        assert "alice" not in r.all_owners()

    def test_replace_unknown_owner_creates_owner(self) -> None:
        """First-time registration via ``replace_owner_bindings`` is
        equivalent to a per-binding ``register`` loop – useful for
        callers that don't want to track whether they've already
        registered."""
        r = ConflictRegistry()
        r.replace_owner_bindings(
            "alice",
            [
                InputBinding(kind="key", identifier="a"),
            ],
        )
        assert r.bindings_for("alice") == [
            InputBinding(kind="key", identifier="a"),
        ]

    def test_replace_unknown_owner_with_empty_set_is_noop(self) -> None:
        r = ConflictRegistry()
        r.replace_owner_bindings("ghost", [])
        assert r.all_owners() == []

    def test_replace_releases_old_bindings_for_other_readers(self) -> None:
        """Bindings that drop out of the new set are no longer
        claimed by ``owner`` – a different owner sees them as
        free."""
        r = ConflictRegistry()
        binding_a = InputBinding(kind="key", identifier="a")
        binding_b = InputBinding(kind="key", identifier="b")
        r.register("alice", binding_a)
        r.register("alice", binding_b)
        r.replace_owner_bindings("alice", [binding_b])
        assert r.is_available(binding_a, owner="bob") is True
        assert r.conflicts_for(binding_b, owner="bob") == ["alice"]

    def test_replace_keeps_other_owners_claims_intact(self) -> None:
        """When two owners share a binding, replacing one's set
        doesn't touch the other's claim on the shared binding."""
        r = ConflictRegistry()
        shared = InputBinding(kind="key", identifier="a")
        r.register("alice", shared)
        r.register("bob", shared)
        r.replace_owner_bindings(
            "alice",
            [
                InputBinding(kind="key", identifier="b"),
            ],
        )
        # ``a`` is no longer alice's, but bob still has it.
        assert r.conflicts_for(shared, owner="charlie") == ["bob"]

    def test_replace_idempotent_with_same_set(self) -> None:
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="a")
        r.register("alice", binding)
        r.replace_owner_bindings("alice", [binding])
        assert r.bindings_for("alice") == [binding]


class TestBindingsForAndAllOwners:
    def test_bindings_for_returns_sorted_snapshot(self) -> None:
        r = ConflictRegistry()
        for ident in ["w", "a", "s"]:
            r.register("alice", InputBinding(kind="key", identifier=ident))
        # Sorted by (kind, identifier) – stable for diagnostics.
        assert r.bindings_for("alice") == [
            InputBinding(kind="key", identifier="a"),
            InputBinding(kind="key", identifier="s"),
            InputBinding(kind="key", identifier="w"),
        ]

    def test_bindings_for_unknown_owner_is_empty(self) -> None:
        r = ConflictRegistry()
        assert r.bindings_for("ghost") == []

    def test_all_owners_returns_unique_sorted(self) -> None:
        r = ConflictRegistry()
        r.register("alice", InputBinding(kind="key", identifier="w"))
        r.register("bob", InputBinding(kind="key", identifier="a"))
        r.register("alice", InputBinding(kind="controller_button", identifier="A"))
        assert r.all_owners() == ["alice", "bob"]


# ---------------------------------------------------------------------------
# default_registry: pre-populates RESERVED_MOVEMENT_KEYS
# ---------------------------------------------------------------------------


class TestDefaultRegistry:
    def test_pre_registers_movement_keys_under_system_owner(self) -> None:
        r = default_registry(RESERVED_MOVEMENT_KEYS)
        bindings = r.bindings_for(SYSTEM_MOVEMENT_OWNER)
        keys_registered = {b.identifier for b in bindings}
        assert keys_registered == set(RESERVED_MOVEMENT_KEYS)
        # Every entry is ``kind="key"`` – movement is a keyboard
        # concept; no controller-button or axis registrations.
        assert all(b.kind == "key" for b in bindings)

    def test_movement_keys_block_other_owners(self) -> None:
        """The whole point of the pre-registration: a future OSC
        HotkeyTrigger trying to bind ``w`` sees ``system:movement``
        as the conflict owner, surfaced to the operator."""
        r = default_registry(RESERVED_MOVEMENT_KEYS)
        binding = InputBinding(kind="key", identifier="w")
        assert r.conflicts_for(binding, owner="osc:hotkey") == [
            SYSTEM_MOVEMENT_OWNER,
        ]

    def test_non_movement_key_is_free_for_other_owners(self) -> None:
        r = default_registry(RESERVED_MOVEMENT_KEYS)
        binding = InputBinding(kind="key", identifier="z")
        assert r.is_available(binding, owner="osc:hotkey") is True

    def test_default_registry_returns_fresh_instance(self) -> None:
        a = default_registry(RESERVED_MOVEMENT_KEYS)
        b = default_registry(RESERVED_MOVEMENT_KEYS)
        a.register("alice", InputBinding(kind="key", identifier="z"))
        assert b.bindings_for("alice") == []

    def test_accepts_set_or_list_or_generator(self) -> None:
        """``default_registry`` widened to ``Iterable[str]`` so callers
        with a ``set`` / ``list`` / generator of keys don't have to
        coerce to ``frozenset`` first."""
        # set
        r1 = default_registry({"a", "b"})
        assert {b.identifier for b in r1.bindings_for(SYSTEM_MOVEMENT_OWNER)} == {"a", "b"}
        # list
        r2 = default_registry(["c", "d"])
        assert {b.identifier for b in r2.bindings_for(SYSTEM_MOVEMENT_OWNER)} == {"c", "d"}
        # generator
        r3 = default_registry(x for x in ["e", "f"])
        assert {b.identifier for b in r3.bindings_for(SYSTEM_MOVEMENT_OWNER)} == {"e", "f"}


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_register_unregister_is_consistent(self) -> None:
        """Spam register / unregister from many threads and assert the
        registry is in a consistent terminal state. No torn reads, no
        deadlocks. Two-thread minimum is enough to exercise the lock;
        this uses a slightly bigger pool to make any race more likely."""
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")

        def worker(owner: str) -> None:
            for _ in range(50):
                r.register(owner, binding)
                r.unregister_owner(owner)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, f"owner-{i}") for i in range(8)]
            for f in futures:
                f.result(timeout=5.0)

        # Terminal state: every worker did a final ``unregister_owner``,
        # so the registry has no claims on this binding.
        assert r.conflicts_for(binding, owner="anyone") == []

    def test_lock_serialises_register_against_conflicts_for(self) -> None:
        """Reader and writer running concurrently never see partial
        state. Asserts the registry doesn't yield half-built
        ``_owners_by_binding`` entries through the read API."""
        r = ConflictRegistry()
        binding = InputBinding(kind="key", identifier="w")
        stop = threading.Event()

        def writer() -> None:
            i = 0
            while not stop.is_set():
                r.register(f"owner-{i % 4}", binding)
                r.unregister_owner(f"owner-{i % 4}")
                i += 1

        def reader_observed_owners() -> set[str]:
            seen: set[str] = set()
            while not stop.is_set():
                seen.update(r.conflicts_for(binding, owner="reader"))
            return seen

        with ThreadPoolExecutor(max_workers=2) as pool:
            w = pool.submit(writer)
            reader = pool.submit(reader_observed_owners)
            # Let the threads run briefly, then stop them.
            stop.wait(0.05)
            stop.set()
            w.result(timeout=5.0)
            reader.result(timeout=5.0)

        # We only assert no exception was raised; the actual contents
        # depend on scheduling, but consistent reads (no garbage owner
        # strings) is what the lock guarantees.


# ---------------------------------------------------------------------------
# MIDI identity in the registry
# ---------------------------------------------------------------------------


class TestMidiIdentifier:
    def test_canonical_four_part_encoding(self) -> None:
        # Patch id / type / channel / number – colon-separated, every
        # component visible in the result so a registry dump is
        # diagnosable without round-tripping through a parser.
        assert midi_identifier(1, "control_change", 1, 7) == "1:control_change:1:7"

    def test_none_number_encodes_as_any(self) -> None:
        # ``program_change`` carries no per-message number; encode
        # the slot as the literal "any" so a missing number can't
        # collide with a real number ``0``.
        assert midi_identifier(2, "program_change", 1, None) == "2:program_change:1:any"

    def test_zero_channel_encodes_as_zero(self) -> None:
        # Channel 0 is the wildcard "any channel" used by
        # MidiMessageTrigger. It encodes verbatim – only the ``number``
        # slot has the explicit "any" sentinel because it can be
        # ``None``; channel is always an int.
        assert midi_identifier(2, "control_change", 0, 7) == "2:control_change:0:7"

    def test_zero_patch_id_encodes_as_any_patch(self) -> None:
        # Patch id 0 is the wildcard "any patch". It encodes
        # verbatim as the int ``0``; the runtime dispatcher matches
        # incoming messages from any patch against it.
        assert midi_identifier(0, "control_change", 1, 7) == "0:control_change:1:7"

    def test_distinct_components_produce_distinct_strings(self) -> None:
        # Conflict semantics rely on string equality of the encoded
        # identifier – two bindings differing in any component must
        # land on distinct strings or the registry would treat them
        # as the same claim.
        seen = {
            midi_identifier(1, "control_change", 1, 7),
            midi_identifier(2, "control_change", 1, 7),  # different patch
            midi_identifier(1, "note_on", 1, 7),  # different type
            midi_identifier(1, "control_change", 2, 7),  # different channel
            midi_identifier(1, "control_change", 1, 8),  # different number
        }
        assert len(seen) == 5

    def test_patch_id_is_int_so_no_separator_collision(self) -> None:
        # ``patch_id`` is a non-negative int, so it can never contain
        # the ``:`` separator – distinct patch ids always encode to
        # distinct strings without any escaping (unlike the old
        # alias-string key). Spot-check multi-digit ids stay distinct.
        patch_ids = [1, 11, 2, 21, 12]
        encoded = {midi_identifier(p, "control_change", 1, 7) for p in patch_ids}
        assert len(encoded) == len(patch_ids)


class TestMidiInputKind:
    def test_registry_accepts_midi_kind(self) -> None:
        # ``"midi"`` is a literal of :data:`InputKind`. The registry's
        # API is otherwise generic over kinds, so this test just confirms
        # the type checker sees ``"midi"`` and the existing register /
        # conflicts_for / bindings_for surface accepts it without
        # special-casing.
        r = ConflictRegistry()
        binding = InputBinding(
            kind="midi",
            identifier=midi_identifier(1, "control_change", 1, 7),
        )
        r.register(OSC_MIDI_OWNER, binding)
        assert binding in r.bindings_for(OSC_MIDI_OWNER)
        assert OSC_MIDI_OWNER in r.conflicts_for(binding, owner="other")

    def test_osc_midi_owner_constant_value(self) -> None:
        # Sibling to the existing ``"osc:hotkey"`` /
        # ``"osc:controller_button"`` owner names. The exact value
        # is part of the registry's diagnostic surface – a future
        # registry-dump endpoint exposes owner names verbatim.
        assert OSC_MIDI_OWNER == "osc:midi"
