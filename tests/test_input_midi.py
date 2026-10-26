# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the USB MIDI subsystem.

Covers the substrate:

* Discovery: empty when mido is unavailable; populates when present;
  conversion of mido port names to :class:`DiscoveredDevice` shape.
* Apply config: open / close / replace ports under the lock;
  ``midi_patch_missing`` status flag flips correctly when a patch
  has no matching device; ``midi_unavailable`` surfaces only once a
  patch is configured.
* Subscribe / unsubscribe: idempotent unsubscribe; multiple
  subscribers receive the same event; raising in one subscriber
  doesn't break the others.
* MIDI Learn: ``arm_capture`` returns the next event; returns ``None``
  on timeout; resets the slot between arms.
* Conversion: every mido message type the OSC trigger model cares
  about (note_on, note_off, control_change, program_change,
  polytouch → key_pressure, aftertouch → channel_pressure); types
  outside the model (pitch_bend, sysex) drop to ``None``.
* Shutdown closes every open port and clears subscribers.

The tests don't import the real ``mido`` – they monkeypatch
:mod:`openfollow.input.midi`'s module-level ``_mido`` to a fake whose
``get_input_names`` and ``open_input`` are scriptable. ``open_input``
returns a fake port that records its callback so the test can pump
synthetic messages through ``port.callback(msg)``.

Devices are referenced by integer MIDI patch id rather than by
alias string. Each test assigns
patch ids 1, 2, 3… in list order and asserts the emitted event
carries that patch's id.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from openfollow.configuration import MidiPatch
from openfollow.input import midi as midi_mod
from openfollow.input.midi import (
    DiscoveredDevice,
    MidiEvent,
    MidiSubsystem,
    _normalize_port_name,
    bound_identifier,
)

# Fake mido harness shared with ``tests/test_midi_alias_hot_reload.py``.
# Shared fake mido harness to avoid duplicate test setup.
from tests._fake_midi import (
    FakeMessage as _FakeMessage,
)
from tests._fake_midi import (
    FakeMido as _FakeMido,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_mido(monkeypatch: pytest.MonkeyPatch) -> _FakeMido:
    """Install a fresh fake ``mido`` for one test."""
    fake = _FakeMido()
    monkeypatch.setattr(midi_mod, "_mido", fake)
    monkeypatch.setattr(midi_mod, "_MIDO_IMPORT_ERROR", None)
    return fake


# ---------------------------------------------------------------------------
# DiscoveredDevice + MidiEvent shape
# ---------------------------------------------------------------------------


class TestDiscoveredDevice:
    def test_identifier_uses_serial_and_port_when_serial_present(self) -> None:
        # One device's ports share its serial, so the port name keeps them apart.
        first = DiscoveredDevice(serial="ABC123", port_name="X", product="X")
        second = DiscoveredDevice(serial="ABC123", port_name="Y", product="Y")
        assert first.identifier == "serial:ABC123|X"
        assert first.identifier != second.identifier

    def test_identifier_falls_back_to_composite_without_serial(self) -> None:
        d = DiscoveredDevice(serial=None, port_name="MIDI Mix", product="MIDI Mix")
        assert d.identifier == "port:MIDI Mix|MIDI Mix"

    def test_open_name_prefers_raw_then_falls_back(self) -> None:
        # The raw OS name carries the volatile ALSA suffix and is what mido
        # opens; a directly-constructed device with no raw name falls back to
        # the (normalized) port_name.
        with_raw = DiscoveredDevice(
            serial=None,
            port_name="Korg:CTRL",
            product="Korg:CTRL",
            raw_port_name="Korg:CTRL 16:0",
        )
        assert with_raw.open_name == "Korg:CTRL 16:0"
        without_raw = DiscoveredDevice(serial=None, port_name="X", product="X")
        assert without_raw.open_name == "X"


class TestNormalizePortName:
    """The ALSA ``client:port`` suffix is volatile across restarts / replug
    and must not be part of a device's saved identity."""

    def test_strips_trailing_alsa_address(self) -> None:
        assert _normalize_port_name("nanoKONTROL2:nanoKONTROL2 _ CTRL 16:0") == "nanoKONTROL2:nanoKONTROL2 _ CTRL"

    def test_strips_regardless_of_address_value(self) -> None:
        # Same device, different ALSA address after a reboot → same identity.
        a = _normalize_port_name("Foo:Bar 16:0")
        b = _normalize_port_name("Foo:Bar 20:1")
        assert a == b == "Foo:Bar"

    def test_passes_through_name_without_suffix(self) -> None:
        # macOS / Windows port names (no ALSA address) are untouched.
        assert _normalize_port_name("MIDI Mix") == "MIDI Mix"

    def test_does_not_strip_internal_colon_numbers(self) -> None:
        # Only a trailing `` N:M`` is an ALSA address; an internal colon
        # (the client:port-name separator) must survive.
        assert _normalize_port_name("X-Touch:X-Touch INT") == "X-Touch:X-Touch INT"


# ---------------------------------------------------------------------------
# Availability + status flags
# ---------------------------------------------------------------------------


class TestAvailability:
    def test_available_true_when_backend_loaded(self, fake_mido: _FakeMido) -> None:
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        assert sub.available is True
        assert flags["midi_unavailable"] is None

    def test_available_false_when_backend_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Badge slot stays clear at construction; the "unavailable" message is deferred to apply_config.
        monkeypatch.setattr(midi_mod, "_mido", None)
        monkeypatch.setattr(
            midi_mod,
            "_MIDO_IMPORT_ERROR",
            "ImportError: no rtmidi",
        )
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        assert sub.available is False
        assert flags["midi_unavailable"] is None

    def test_backend_missing_with_no_patches_stays_silent(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An install with absent backend and no configured patches carries no badge."""
        monkeypatch.setattr(midi_mod, "_mido", None)
        monkeypatch.setattr(midi_mod, "_MIDO_IMPORT_ERROR", "ImportError: no rtmidi")
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config([])
        assert flags["midi_unavailable"] is None
        assert flags.get("midi_patch_missing") is None

    def test_backend_missing_with_patches_surfaces_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Once the operator configures any MIDI patch, an absent backend is
        worth surfacing – the patch can't work and the badge explains why."""
        monkeypatch.setattr(midi_mod, "_mido", None)
        monkeypatch.setattr(midi_mod, "_MIDO_IMPORT_ERROR", "ImportError: no rtmidi")
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config([MidiPatch(id=1, alias="Workspace 1", port_name="MIDI Mix", product="MIDI Mix")])
        assert "no rtmidi" in (flags["midi_unavailable"] or "")

    def test_default_status_flags_dict(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        # No external dict provided – subsystem still tracks state internally.
        sub = MidiSubsystem()
        assert sub.available is True


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscover:
    def test_returns_empty_when_backend_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(midi_mod, "_mido", None)
        sub = MidiSubsystem()
        assert sub.discover() == []

    def test_returns_devices_from_mido_port_list(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix", "X-Touch Mini"]
        sub = MidiSubsystem()
        devices = sub.discover()
        assert len(devices) == 2
        assert devices[0].port_name == "MIDI Mix"
        assert devices[0].product == "MIDI Mix"
        assert devices[0].serial is None
        assert devices[1].port_name == "X-Touch Mini"
        # Plain names carry no ALSA suffix → raw == normalized.
        assert devices[0].raw_port_name == "MIDI Mix"

    def test_discover_normalizes_alsa_suffix_and_keeps_raw(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["nanoKONTROL2:nanoKONTROL2 _ CTRL 16:0"]
        sub = MidiSubsystem()
        d = sub.discover()[0]
        # Identity is the normalized name (stable across restarts)…
        assert d.port_name == "nanoKONTROL2:nanoKONTROL2 _ CTRL"
        assert d.product == "nanoKONTROL2:nanoKONTROL2 _ CTRL"
        assert d.identifier == ("port:nanoKONTROL2:nanoKONTROL2 _ CTRL|nanoKONTROL2:nanoKONTROL2 _ CTRL")
        # …but the raw OS name (for open_input) keeps the ALSA suffix.
        assert d.raw_port_name == "nanoKONTROL2:nanoKONTROL2 _ CTRL 16:0"
        assert d.open_name == "nanoKONTROL2:nanoKONTROL2 _ CTRL 16:0"

    def test_returns_empty_on_mido_exception(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.discover_raises = True
        sub = MidiSubsystem()
        assert sub.discover() == []

    def test_runtime_backend_failure_records_midi_unavailable(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        """Lazy rtmidi backend loading can fail on first get_input_names() call."""
        fake_mido.discover_raises = True
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        assert sub.available is True
        assert sub.discover() == []
        assert sub.available is False
        assert "MIDI backend error" in (flags["midi_unavailable"] or "")
        assert "simulated discover failure" in (flags["midi_unavailable"] or "")

    def test_backend_failure_clears_stale_patch_missing(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        # First pass: patch has no matching port → flag goes truthy.
        fake_mido.input_names = []
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="Workspace 1",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        assert flags["midi_patch_missing"] is not None
        # Second pass: backend fails. The route must clear the stale
        # patch-missing flag in addition to setting unavailable.
        fake_mido.discover_raises = True
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="Workspace 1",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        assert "MIDI backend error" in (flags["midi_unavailable"] or "")
        assert flags["midi_patch_missing"] is None

    def test_runtime_backend_failure_does_not_taint_patch_missing(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        """When discovery fails at the backend level, every patch
        would otherwise look "missing" – but the real problem is the
        backend, not the hardware list. ``apply_config`` short-circuits
        on ``midi_unavailable`` so ``midi_patch_missing`` stays clean
        and the badge points at one root cause instead of a list of
        spurious patches."""
        fake_mido.discover_raises = True
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="Workspace 1",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        assert "MIDI backend error" in (flags["midi_unavailable"] or "")
        # The patch-missing slot was never set – nothing was opened
        # and the badge surface gets one signal, not two competing
        # ones.
        assert flags.get("midi_patch_missing") is None

    def test_patch_missing_list_sorts_by_numeric_id(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        """Missing patches are listed in numeric order, not lexicographic."""
        fake_mido.input_names = []  # nothing connected → every patch missing
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config(
            [
                MidiPatch(id=2, alias="Two", port_name="P2", product="P2"),
                MidiPatch(id=10, alias="Ten", port_name="P10", product="P10"),
            ]
        )
        msg = flags["midi_patch_missing"] or ""
        assert msg.index("Two") < msg.index("Ten")  # id 2 before id 10

    def test_successful_discover_clears_prior_unavailable_flag(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        # First call: backend fails, flag is set, available flips False.
        fake_mido.discover_raises = True
        assert sub.discover() == []
        assert sub.available is False
        # Backend recovers; second call enumerates cleanly. The flag
        # clears and ``available`` flips back to True without needing
        # a fresh ``MidiSubsystem`` instance.
        fake_mido.discover_raises = False
        fake_mido.input_names = ["MIDI Mix"]
        devices = sub.discover()
        assert [d.port_name for d in devices] == ["MIDI Mix"]
        assert flags["midi_unavailable"] is None
        assert sub.available is True


# ---------------------------------------------------------------------------
# apply_config: open / close / replace lifecycle
# ---------------------------------------------------------------------------


class TestApplyConfig:
    def test_opens_port_for_matching_patch(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="Workspace 1",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        assert len(fake_mido.opened) == 1
        assert fake_mido.opened[0].name == "MIDI Mix"
        assert flags["midi_patch_missing"] is None

    def test_records_missing_patch_in_status_flags(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = []  # nothing connected
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="Workspace 1",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        assert fake_mido.opened == []
        assert "Workspace 1" in (flags["midi_patch_missing"] or "")

    def test_unavailable_backend_does_not_taint_patch_missing_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # When mido backend never imported, skip patch resolution to avoid false "missing" warnings.
        monkeypatch.setattr(midi_mod, "_mido", None)
        monkeypatch.setattr(
            midi_mod,
            "_MIDO_IMPORT_ERROR",
            "ImportError: no rtmidi",
        )
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="Workspace 1",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        # ``midi_unavailable`` carries the actual diagnostic;
        # ``midi_patch_missing`` stays untouched by apply_config
        # because the patch list isn't the issue.
        assert "no rtmidi" in (flags["midi_unavailable"] or "")
        assert flags.get("midi_patch_missing") is None

    def test_idempotent_reapply_keeps_port_open(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        patch = MidiPatch(
            id=1,
            alias="A",
            port_name="MIDI Mix",
            product="MIDI Mix",
        )
        sub.apply_config([patch])
        sub.apply_config([patch])  # same patch → no-op
        assert len(fake_mido.opened) == 1
        assert not fake_mido.opened[0].closed

    def test_accepts_one_shot_iterator(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        """``apply_config`` materializes its argument once (into
        ``self._patches``) and iterates that, so a one-shot iterator still
        applies fully instead of collapsing to an empty config on a second
        pass."""
        fake_mido.input_names = ["MIDI Mix"]
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        patches = iter([MidiPatch(id=1, alias="Workspace 1", port_name="MIDI Mix", product="MIDI Mix")])
        sub.apply_config(patches)
        assert len(fake_mido.opened) == 1
        assert flags["midi_patch_missing"] is None

    def test_empty_config_with_healthy_backend_skips_discovery(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        """With a working backend but no configured patches, ``apply_config``
        clears both badges and skips discovery entirely – so a backend that
        would error at enumeration can't surface a warning for MIDI the
        operator isn't using (mirrors the import-absent no-patch path)."""
        fake_mido.discover_raises = True
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config([])
        # Direct proof the enumeration was skipped (not merely that it didn't
        # error): a regression that called discover() but happened to succeed
        # would slip past a flag-only assertion.
        assert fake_mido.get_input_names_calls == 0
        assert flags["midi_unavailable"] is None
        assert flags["midi_patch_missing"] is None

    def test_clearing_all_patches_closes_ports_and_clears_badges(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        """Removing all MIDI patches closes listener ports and drops badges."""
        fake_mido.input_names = ["MIDI Mix"]
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config([MidiPatch(id=1, alias="A", port_name="MIDI Mix", product="MIDI Mix")])
        assert len(fake_mido.opened) == 1
        sub.apply_config([])
        assert fake_mido.opened[0].closed
        assert flags["midi_unavailable"] is None
        assert flags["midi_patch_missing"] is None


class TestPollHotplug:
    """``poll_hotplug`` re-applies the stored patch set when the connected
    MIDI port set changes, so the badge / listener ports track an unplug or
    replug without an explicit config save (USB MIDI has no event-based
    hotplug). Throttled so the per-tick caller stays cheap."""

    @staticmethod
    def _patch() -> MidiPatch:
        return MidiPatch(id=1, alias="Workspace 1", port_name="MIDI Mix", product="MIDI Mix")

    def _fixed_clock(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
        clock = {"t": 1000.0}
        monkeypatch.setattr(midi_mod.time, "monotonic", lambda: clock["t"])
        return clock

    def test_noop_when_backend_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(midi_mod, "_mido", None)
        monkeypatch.setattr(midi_mod, "_MIDO_IMPORT_ERROR", "ImportError: no rtmidi")
        sub = MidiSubsystem()
        sub.poll_hotplug()  # must not raise
        assert sub._status_flags.get("midi_patch_missing") is None

    def test_throttled_within_interval_does_not_reapply(
        self,
        fake_mido: _FakeMido,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = self._fixed_clock(monkeypatch)
        fake_mido.input_names = ["MIDI Mix"]
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config([self._patch()])
        assert flags["midi_patch_missing"] is None
        # First poll establishes the throttle timestamp (port set unchanged).
        sub.poll_hotplug()
        # Now unplug, but a second poll inside the throttle window is a no-op.
        fake_mido.input_names = []
        clock["t"] = 1001.0  # +1 s < _HOTPLUG_POLL_INTERVAL_S (2 s)
        sub.poll_hotplug()
        assert flags["midi_patch_missing"] is None  # throttled, not re-applied

    def test_detects_unplug_after_interval(
        self,
        fake_mido: _FakeMido,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = self._fixed_clock(monkeypatch)
        fake_mido.input_names = ["MIDI Mix"]
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config([self._patch()])
        assert len(fake_mido.opened) == 1
        assert flags["midi_patch_missing"] is None
        # Unplug + advance past the throttle → re-apply marks the patch missing
        # and closes the now-orphaned listener port.
        fake_mido.input_names = []
        clock["t"] = 1003.0
        sub.poll_hotplug()
        assert "Workspace 1" in (flags["midi_patch_missing"] or "")
        assert 1 not in sub._open_ports
        assert fake_mido.opened[0].closed

    def test_detects_replug_clears_flag(
        self,
        fake_mido: _FakeMido,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = self._fixed_clock(monkeypatch)
        fake_mido.input_names = []  # start disconnected
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config([self._patch()])
        assert "Workspace 1" in (flags["midi_patch_missing"] or "")
        # Replug + advance → re-apply opens the port and clears the flag.
        fake_mido.input_names = ["MIDI Mix"]
        clock["t"] = 1003.0
        sub.poll_hotplug()
        assert flags["midi_patch_missing"] is None
        assert 1 in sub._open_ports

    def test_no_reapply_when_port_set_unchanged(
        self,
        fake_mido: _FakeMido,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = self._fixed_clock(monkeypatch)
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config([self._patch()])
        opened_before = len(fake_mido.opened)
        # Past the throttle, but the same port set → no re-apply, so no extra
        # open_input call.
        clock["t"] = 1003.0
        sub.poll_hotplug()
        assert len(fake_mido.opened) == opened_before

    def test_no_patches_caches_port_set_so_poll_does_not_churn(
        self,
        fake_mido: _FakeMido,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = self._fixed_clock(monkeypatch)
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config([])  # no patches → early-exit leaves _last_input_names None
        assert sub._last_input_names is None
        # First poll past the throttle caches the connected set …
        clock["t"] = 1003.0
        sub.poll_hotplug()
        assert sub._last_input_names == frozenset(["MIDI Mix"])
        # … so a second poll with the same set is a true no-op: it early-returns
        # before re-applying. Spy on apply_config to prove it isn't re-invoked.
        reapplied: list[object] = []
        monkeypatch.setattr(sub, "apply_config", lambda patches: reapplied.append(patches))
        clock["t"] = 1006.0
        sub.poll_hotplug()
        assert reapplied == []

    def test_backend_error_during_poll_is_swallowed(
        self,
        fake_mido: _FakeMido,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = self._fixed_clock(monkeypatch)
        fake_mido.input_names = ["MIDI Mix"]
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config([self._patch()])
        # ``get_input_names`` raises during the poll → swallowed, no crash and
        # no re-apply (the missing flag is left as the last apply set it).
        fake_mido.discover_raises = True
        clock["t"] = 1003.0
        sub.poll_hotplug()  # must not raise
        assert flags["midi_patch_missing"] is None

    def test_close_patch_removed_from_config(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        patch = MidiPatch(
            id=1,
            alias="A",
            port_name="MIDI Mix",
            product="MIDI Mix",
        )
        sub.apply_config([patch])
        first_port = fake_mido.opened[0]
        sub.apply_config([])  # patch removed
        assert first_port.closed is True

    def test_replace_when_port_changes(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        first_port = fake_mido.opened[0]
        # Operator unplugs MIDI Mix, plugs in X-Touch Mini, retargets the patch.
        fake_mido.input_names = ["X-Touch Mini"]
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="X-Touch Mini",
                    product="X-Touch Mini",
                ),
            ]
        )
        assert first_port.closed is True
        assert len(fake_mido.opened) == 2
        assert fake_mido.opened[1].name == "X-Touch Mini"

    def test_skips_patches_with_no_device(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        # A patch with no device assigned yet (no port_name / serial) is
        # skipped: neither opened nor reported missing.
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(id=1, alias="Unassigned"),
            ]
        )
        assert fake_mido.opened == []

    def test_open_failure_logged_and_continues(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        """Failed open_input() surfaces patch as missing instead of silently dropped."""
        fake_mido.input_names = ["MIDI Mix", "X-Touch Mini"]
        fake_mido.open_raises_for = {"MIDI Mix"}
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
                MidiPatch(
                    id=2,
                    alias="B",
                    port_name="X-Touch Mini",
                    product="X-Touch Mini",
                ),
            ]
        )
        # First open raised → only "B" opened.
        assert [p.name for p in fake_mido.opened] == ["X-Touch Mini"]
        # Patch ``A`` matched a discovered device but couldn't be opened –
        # the badge surfaces it (by its label) the same way as a never-
        # connected patch rather than leaving the operator wondering why
        # MIDI events from that device aren't firing.
        assert "A" in (flags["midi_patch_missing"] or "")
        # ``B`` opened cleanly – the missing list contains only ``A``.
        assert "B" not in (flags["midi_patch_missing"] or "")

    def test_detach_unknown_patch_is_no_op(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        # Defensive: detaching an id that was already popped must tolerate the
        # missing entry without raising and stage nothing to close.
        sub = MidiSubsystem()
        staged: list = []
        sub._detach_port(999, staged)  # never-opened patch id – must not raise
        assert staged == []

    def test_dropping_patch_detaches_callback_and_closes(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        """Deadlock fix: a dropped patch's port has its callback detached and is
        closed (the close runs outside self._lock)."""
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem(status_flags={})
        sub.apply_config([MidiPatch(id=1, alias="A", port_name="MIDI Mix", product="MIDI Mix")])
        port = fake_mido.opened[0]
        assert port.callback is not None
        sub.apply_config([])  # drop the patch
        assert port.closed is True
        assert port.callback is None

    def test_close_failure_logged_not_raised(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        # Make ``close`` raise to exercise the failure path.
        fake_mido.opened[0].close = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            RuntimeError("boom"),
        )
        # Reapply with the patch dropped – close should swallow the exception.
        sub.apply_config([])

    def test_re_close_when_target_disappears(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        patch = MidiPatch(
            id=1,
            alias="A",
            port_name="MIDI Mix",
            product="MIDI Mix",
        )
        sub.apply_config([patch])
        first_port = fake_mido.opened[0]
        # Operator unplugs MIDI Mix without changing the config.
        fake_mido.input_names = []
        sub.apply_config([patch])  # same patch, target now missing
        assert first_port.closed is True

    def test_apply_opens_raw_name_not_normalized(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        # A patch stores the normalized identity; apply must open the *raw* OS
        # port name (with the ALSA suffix) – opening the normalized name would
        # fail because mido has no such port.
        fake_mido.input_names = ["nanoKONTROL2:nanoKONTROL2 _ CTRL 16:0"]
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="Korg",
                    port_name="nanoKONTROL2:nanoKONTROL2 _ CTRL",
                    product="nanoKONTROL2:nanoKONTROL2 _ CTRL",
                ),
            ]
        )
        assert flags["midi_patch_missing"] is None
        assert len(fake_mido.opened) == 1
        assert fake_mido.opened[0].name == "nanoKONTROL2:nanoKONTROL2 _ CTRL 16:0"

    def test_identity_survives_alsa_address_change(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        # The core fix: across a restart the same patch (normalized identity)
        # still matches the same device after its ALSA client:port number
        # shifts, and the stale port reopens on the new raw address.
        patch = MidiPatch(
            id=1,
            alias="Korg",
            port_name="nanoKONTROL2:nanoKONTROL2 _ CTRL",
            product="nanoKONTROL2:nanoKONTROL2 _ CTRL",
        )
        flags: dict[str, str | None] = {}
        sub = MidiSubsystem(status_flags=flags)
        fake_mido.input_names = ["nanoKONTROL2:nanoKONTROL2 _ CTRL 16:0"]
        sub.apply_config([patch])
        assert flags["midi_patch_missing"] is None
        first = fake_mido.opened[0]
        assert first.name.endswith("16:0")
        # Reboot/replug shifts the ALSA address; identity is unchanged.
        fake_mido.input_names = ["nanoKONTROL2:nanoKONTROL2 _ CTRL 22:0"]
        sub.apply_config([patch])
        assert flags["midi_patch_missing"] is None  # still matched
        assert first.closed is True  # stale port closed
        assert len(fake_mido.opened) == 2
        assert fake_mido.opened[1].name.endswith("22:0")

    def test_no_reopen_when_alsa_address_unchanged(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        # Same address across applies → idempotent (no churn).
        patch = MidiPatch(id=1, port_name="Foo:Bar", product="Foo:Bar")
        fake_mido.input_names = ["Foo:Bar 16:0"]
        sub = MidiSubsystem()
        sub.apply_config([patch])
        sub.apply_config([patch])
        assert len(fake_mido.opened) == 1
        assert not fake_mido.opened[0].closed


# ---------------------------------------------------------------------------
# Patch matching: serial / composite / port-only fallback
# ---------------------------------------------------------------------------


class TestMatch:
    def test_serial_match_wins(self) -> None:
        patch = MidiPatch(id=1, alias="A", serial="ABC", port_name="X")
        discovered = [
            DiscoveredDevice(serial="ABC", port_name="Y", product="Y"),
            DiscoveredDevice(serial=None, port_name="X", product="X"),
        ]
        match = MidiSubsystem._match(patch, discovered)
        assert match is not None and match.port_name == "Y"

    def test_falls_through_to_composite_when_no_serial_match(self) -> None:
        patch = MidiPatch(
            id=1,
            alias="A",
            port_name="MIDI Mix",
            product="MIDI Mix",
        )
        discovered = [
            DiscoveredDevice(serial=None, port_name="MIDI Mix", product="MIDI Mix"),
        ]
        match = MidiSubsystem._match(patch, discovered)
        assert match is not None
        assert match.port_name == "MIDI Mix"

    def test_falls_through_to_port_name_only(self) -> None:
        patch = MidiPatch(
            id=1,
            alias="A",
            port_name="MIDI Mix",
            product="DIFFERENT",
        )
        discovered = [
            DiscoveredDevice(
                serial=None,
                port_name="MIDI Mix",
                product="MIDI Mix",
            ),
        ]
        match = MidiSubsystem._match(patch, discovered)
        assert match is not None
        assert match.port_name == "MIDI Mix"

    def test_returns_none_when_nothing_matches(self) -> None:
        patch = MidiPatch(
            id=1,
            alias="A",
            port_name="X",
            product="Y",
        )
        match = MidiSubsystem._match(patch, [])
        assert match is None

    def test_port_name_fallback_skipped_when_patch_has_none(self) -> None:
        # Patch with empty port_name doesn't enter the port-name-only
        # fallback.
        patch = MidiPatch(id=1, alias="A", port_name="", product="")
        discovered = [
            DiscoveredDevice(serial=None, port_name="X", product="X"),
        ]
        match = MidiSubsystem._match(patch, discovered)
        assert match is None

    def test_port_name_fallback_loop_continues_on_mismatch(self) -> None:
        # Patch has port_name "X" but no discovered device shares that
        # port_name (their port_names are "Y"). Inner ``if`` False → loop
        # continues to next iteration.
        patch = MidiPatch(
            id=1,
            alias="A",
            port_name="X",
            product="OTHER",
        )
        discovered = [
            DiscoveredDevice(serial=None, port_name="Y", product="Y"),
            DiscoveredDevice(serial=None, port_name="Z", product="Z"),
        ]
        match = MidiSubsystem._match(patch, discovered)
        assert match is None

    def test_serial_mismatch_falls_through(self) -> None:
        # Patch has a serial, but no discovered device has that serial.
        # Falls through to composite – and matches because port+product
        # line up.
        patch = MidiPatch(
            id=1,
            alias="A",
            serial="OLD",
            port_name="MIDI Mix",
            product="MIDI Mix",
        )
        discovered = [
            DiscoveredDevice(serial="NEW", port_name="MIDI Mix", product="MIDI Mix"),
        ]
        match = MidiSubsystem._match(patch, discovered)
        assert match is not None


class TestMatchSerialPorts:
    """One USB device may carry several ports, all with its serial."""

    def test_the_patch_port_wins_among_one_devices_ports(self) -> None:
        patch = MidiPatch(id=1, serial="S", port_name="Pad:Pad MIDI 2")
        discovered = [
            DiscoveredDevice(serial="S", port_name="Pad:Pad MIDI 1", product="Pad:Pad MIDI 1"),
            DiscoveredDevice(serial="S", port_name="Pad:Pad MIDI 2", product="Pad:Pad MIDI 2"),
        ]
        match = MidiSubsystem._match(patch, discovered)
        assert match is not None and match.port_name == "Pad:Pad MIDI 2"

    def test_a_device_whose_port_is_renamed_still_matches_by_serial(self) -> None:
        patch = MidiPatch(id=1, serial="S", port_name="Old name")
        discovered = [
            DiscoveredDevice(serial="T", port_name="Old name", product="Old name"),
            DiscoveredDevice(serial="S", port_name="New name", product="New name"),
        ]
        match = MidiSubsystem._match(patch, discovered)
        assert match is not None and match.serial == "S"


class TestBoundIdentifier:
    """What the patch row's Device dropdown shows as selected."""

    _DEVICES = [
        {"identifier": "serial:S1|XTM", "port_name": "XTM", "product": "XTM", "serial": "S1"},
        {"identifier": "serial:S2|XTM", "port_name": "XTM", "product": "XTM", "serial": "S2"},
    ]

    def test_an_unbound_patch_selects_nothing(self) -> None:
        assert bound_identifier(MidiPatch(id=1), self._DEVICES) == ""

    def test_a_patch_selects_its_own_unit(self) -> None:
        patch = MidiPatch(id=1, serial="S2", port_name="XTM")
        assert bound_identifier(patch, self._DEVICES) == "serial:S2|XTM"

    def test_a_patch_saved_without_a_serial_selects_the_device_it_opens(self) -> None:
        patch = MidiPatch(id=1, port_name="XTM")
        assert bound_identifier(patch, self._DEVICES) == "serial:S1|XTM"

    def test_a_patch_whose_device_is_absent_keeps_its_own(self) -> None:
        patch = MidiPatch(id=1, port_name="Gone")
        assert bound_identifier(patch, self._DEVICES) == "port:Gone|Gone"


# ---------------------------------------------------------------------------
# USB serial on Linux: port name -> ALSA card -> USB device -> serial
# ---------------------------------------------------------------------------

_XHCI = Path("platform") / "axi" / "1000120000.pcie" / "1f00200000.usb" / "xhci-hcd.0" / "usb1"


def _usb_midi(sysfs: Path, usb_name: str, cards: dict[int, str], serial: str | None) -> None:
    """A USB device ``usb_name`` with one sound card per ``{card: interface}``."""
    device = sysfs / "devices" / _XHCI / usb_name
    device.mkdir(parents=True, exist_ok=True)
    (device / "devpath").write_text(usb_name.split("-", 1)[1] + "\n")
    (device / "busnum").write_text("1\n")
    if serial is not None:
        (device / "serial").write_text(serial + "\n")
    for card, interface in cards.items():
        target = device / f"{usb_name}:{interface}"
        target.mkdir(parents=True, exist_ok=True)
        entry = sysfs / "class" / "sound" / f"card{card}"
        entry.mkdir(parents=True)
        (entry / "device").symlink_to(target)


@pytest.fixture
def linux_sysfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(midi_mod, "_SYSFS", tmp_path)
    monkeypatch.setattr(midi_mod.sys, "platform", "linux")
    return tmp_path


@pytest.fixture
def serial_reads(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """The sound cards whose USB serial discovery looks up, in order."""
    reads: list[str | None] = []
    real = midi_mod.usb_serial

    def counting(node: str | None, **kwargs: object) -> str | None:
        reads.append(node)
        return real(node, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(midi_mod, "usb_serial", counting)
    return reads


class TestDiscoverUsbSerial:
    def test_a_port_carries_the_serial_of_its_usb_device(self, fake_mido: _FakeMido, linux_sysfs: Path) -> None:
        # Cards 0 and 1 are a Pi's HDMI outputs, so a USB device is card 2: client 24.
        _usb_midi(linux_sysfs, "1-1", {2: "1.0"}, "MM-0042")
        fake_mido.input_names = ["MIDI Mix:MIDI Mix MIDI 1 24:0"]
        (device,) = MidiSubsystem().discover()
        assert device.serial == "MM-0042"
        assert device.port_name == "MIDI Mix:MIDI Mix MIDI 1"
        assert device.raw_port_name == "MIDI Mix:MIDI Mix MIDI 1 24:0"

    @pytest.mark.parametrize("first", ["S1", "S2"])
    def test_two_devices_of_one_model_each_keep_their_patch(
        self, fake_mido: _FakeMido, linux_sysfs: Path, first: str
    ) -> None:
        # Plug-in order decides which unit becomes card 2.
        second = "S2" if first == "S1" else "S1"
        _usb_midi(linux_sysfs, "1-1", {2: "1.0"}, first)
        _usb_midi(linux_sysfs, "1-2", {3: "1.0"}, second)
        fake_mido.input_names = ["X-TOUCH MINI:X-TOUCH MINI MIDI 1 24:0", "X-TOUCH MINI:X-TOUCH MINI MIDI 1 28:0"]
        port = "X-TOUCH MINI:X-TOUCH MINI MIDI 1"
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(id=1, alias="Left", serial="S1", port_name=port),
                MidiPatch(id=2, alias="Right", serial="S2", port_name=port),
            ]
        )
        address = {first: "24:0", second: "28:0"}
        assert sub._open_ports[1].port_name == f"{port} {address['S1']}"
        assert sub._open_ports[2].port_name == f"{port} {address['S2']}"

    def test_a_device_without_a_serial_matches_by_port_name(self, fake_mido: _FakeMido, linux_sysfs: Path) -> None:
        _usb_midi(linux_sysfs, "1-1", {2: "1.0"}, None)
        fake_mido.input_names = ["nanoKONTROL2:nanoKONTROL2 _ CTRL 24:0"]
        sub = MidiSubsystem()
        (device,) = sub.discover()
        assert device.serial is None
        sub.apply_config([MidiPatch(id=1, alias="K", port_name="nanoKONTROL2:nanoKONTROL2 _ CTRL")])
        assert [p.name for p in fake_mido.opened] == ["nanoKONTROL2:nanoKONTROL2 _ CTRL 24:0"]

    def test_every_card_of_one_device_carries_its_serial(self, fake_mido: _FakeMido, linux_sysfs: Path) -> None:
        _usb_midi(linux_sysfs, "1-1", {2: "1.0", 3: "1.3"}, "ONE")
        fake_mido.input_names = ["Dev:Dev MIDI 1 24:0", "Dev MIDI:Dev MIDI MIDI 1 28:0"]
        assert [d.serial for d in MidiSubsystem().discover()] == ["ONE", "ONE"]

    def test_each_port_of_one_device_keeps_its_own_patch(self, fake_mido: _FakeMido, linux_sysfs: Path) -> None:
        _usb_midi(linux_sysfs, "1-1", {2: "1.0"}, "LP-7")
        fake_mido.input_names = ["Launchpad:Launchpad MIDI 1 24:0", "Launchpad:Launchpad MIDI 2 24:1"]
        sub = MidiSubsystem()
        devices = sub.discover()
        assert len({d.identifier for d in devices}) == 2
        sub.apply_config(
            [
                MidiPatch(id=1, alias="Notes", serial="LP-7", port_name="Launchpad:Launchpad MIDI 1"),
                MidiPatch(id=2, alias="Lights", serial="LP-7", port_name="Launchpad:Launchpad MIDI 2"),
            ]
        )
        assert sub._open_ports[1].port_name == "Launchpad:Launchpad MIDI 1 24:0"
        assert sub._open_ports[2].port_name == "Launchpad:Launchpad MIDI 2 24:1"

    def test_a_card_is_read_once_for_all_its_ports(
        self, fake_mido: _FakeMido, linux_sysfs: Path, serial_reads: list[str | None]
    ) -> None:
        _usb_midi(linux_sysfs, "1-1", {2: "1.0"}, "LP-7")
        fake_mido.input_names = ["Launchpad:Launchpad MIDI 1 24:0", "Launchpad:Launchpad MIDI 2 24:1"]
        assert [d.serial for d in MidiSubsystem().discover()] == ["LP-7", "LP-7"]
        assert serial_reads == ["card2"]

    @pytest.mark.parametrize(
        ("name", "card"),
        [
            ("Card Zero:Card Zero MIDI 1 16:0", 0),
            ("Card Zero:Card Zero MIDI 2 19:0", 0),
            ("Card One:Card One MIDI 1 20:0", 1),
            ("Card Last:Card Last MIDI 1 127:0", 27),
        ],
    )
    def test_the_static_client_range_maps_to_its_card(
        self, fake_mido: _FakeMido, linux_sysfs: Path, name: str, card: int
    ) -> None:
        for n in range(29):
            _usb_midi(linux_sysfs, f"1-{n + 1}", {n: "1.0"}, f"CARD{n}")
        fake_mido.input_names = [name]
        assert [d.serial for d in MidiSubsystem().discover()] == [f"CARD{card}"]

    @pytest.mark.parametrize(
        "name",
        [
            "MIDI Mix",  # no sequencer address (macOS / Windows shape)
            "MIDI Mix:MIDI Mix MIDI 1 24",  # no port number
            "MIDI Mix:MIDI Mix MIDI 1 24:x",
            "MIDI Mix:MIDI Mix MIDI 1 24:0 extra",
            "Midi Through:Midi Through Port-0 14:0",  # a global client, no card
            "Virtual:Virtual out 128:0",  # an application's client
        ],
    )
    def test_a_port_name_that_names_no_card_has_no_serial(
        self, fake_mido: _FakeMido, linux_sysfs: Path, serial_reads: list[str | None], name: str
    ) -> None:
        # Every card a wrong parse could land on reports a serial.
        for card in range(29):
            _usb_midi(linux_sysfs, f"1-{card + 1}", {card: "1.0"}, f"WRONG{card}")
        fake_mido.input_names = [name]
        (device,) = MidiSubsystem().discover()
        assert device.serial is None
        assert serial_reads == []

    def test_a_card_missing_from_sysfs_has_no_serial(self, fake_mido: _FakeMido, linux_sysfs: Path) -> None:
        fake_mido.input_names = ["MIDI Mix:MIDI Mix MIDI 1 24:0"]
        (device,) = MidiSubsystem().discover()
        assert device.serial is None
        assert device.port_name == "MIDI Mix:MIDI Mix MIDI 1"

    def test_an_unreadable_sysfs_never_fails_discovery(
        self, fake_mido: _FakeMido, linux_sysfs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _usb_midi(linux_sysfs, "1-1", {2: "1.0"}, "MM-0042")
        real_resolve = Path.resolve

        def denied(self: Path, *args: object, **kwargs: object) -> Path:
            if self.name == "device":
                raise PermissionError(13, "Permission denied", str(self))
            return real_resolve(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "resolve", denied)
        fake_mido.input_names = ["MIDI Mix:MIDI Mix MIDI 1 24:0"]
        (device,) = MidiSubsystem().discover()
        assert device.serial is None

    def test_off_linux_no_port_has_a_serial(
        self, fake_mido: _FakeMido, linux_sysfs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _usb_midi(linux_sysfs, "1-1", {2: "1.0"}, "MM-0042")
        monkeypatch.setattr(midi_mod.sys, "platform", "darwin")
        fake_mido.input_names = ["MIDI Mix:MIDI Mix MIDI 1 24:0"]
        (device,) = MidiSubsystem().discover()
        assert device.serial is None
        assert device.identifier == "port:MIDI Mix:MIDI Mix MIDI 1|MIDI Mix:MIDI Mix MIDI 1"


# ---------------------------------------------------------------------------
# Subscribe / dispatch
# ---------------------------------------------------------------------------


class TestSubscribeDispatch:
    def test_subscriber_receives_event(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        received: list[MidiEvent] = []
        sub.subscribe(received.append)
        port = fake_mido.opened[0]
        assert port.callback is not None
        port.callback(_FakeMessage(type="note_on", channel=0, note=60, velocity=100))
        assert len(received) == 1
        assert received[0].type == "note_on"
        assert received[0].channel == 1  # 0-indexed → 1-indexed
        assert received[0].number == 60
        assert received[0].value == 100
        assert received[0].patch_id == 1

    def test_unsubscribe_removes_callback(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        received: list[MidiEvent] = []
        unsubscribe = sub.subscribe(received.append)
        unsubscribe()
        port = fake_mido.opened[0]
        assert port.callback is not None
        port.callback(_FakeMessage(type="note_on", note=60))
        assert received == []

    def test_unsubscribe_idempotent(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        sub = MidiSubsystem()
        unsubscribe = sub.subscribe(lambda _e: None)
        unsubscribe()
        unsubscribe()  # second call must not raise

    def test_multiple_subscribers_each_receive(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        a_seen: list[MidiEvent] = []
        b_seen: list[MidiEvent] = []
        sub.subscribe(a_seen.append)
        sub.subscribe(b_seen.append)
        port = fake_mido.opened[0]
        assert port.callback is not None
        port.callback(_FakeMessage(type="control_change", control=42, value=64))
        assert len(a_seen) == 1
        assert len(b_seen) == 1

    def test_one_subscriber_raising_does_not_break_others(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        good: list[MidiEvent] = []
        sub.subscribe(lambda _e: (_ for _ in ()).throw(RuntimeError("boom")))
        sub.subscribe(good.append)
        port = fake_mido.opened[0]
        assert port.callback is not None
        port.callback(_FakeMessage(type="note_on", note=60, velocity=64))
        assert len(good) == 1


# ---------------------------------------------------------------------------
# Always-on recent-event ring
# ---------------------------------------------------------------------------


class TestRecentEventsRing:
    @staticmethod
    def _open(fake_mido: _FakeMido) -> tuple[MidiSubsystem, object]:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config([MidiPatch(id=1, alias="A", port_name="MIDI Mix", product="MIDI Mix")])
        port = fake_mido.opened[0]
        assert port.callback is not None
        return sub, port

    def test_empty_before_any_event(self, fake_mido: _FakeMido) -> None:
        assert MidiSubsystem().recent_events() == []

    def test_records_events_oldest_first(self, fake_mido: _FakeMido) -> None:
        sub, port = self._open(fake_mido)
        port.callback(_FakeMessage(type="note_on", note=60, velocity=10))
        port.callback(_FakeMessage(type="control_change", control=7, value=20))
        events = sub.recent_events()
        assert [e.type for e in events] == ["note_on", "control_change"]
        assert events[0].number == 60
        assert events[1].value == 20

    def test_records_without_subscriber(self, fake_mido: _FakeMido) -> None:
        # The ring is always-on – events are recorded even when no
        # learn capture is armed and no subscriber is registered.
        sub, port = self._open(fake_mido)
        port.callback(_FakeMessage(type="note_on", note=64, velocity=99))
        assert len(sub.recent_events()) == 1

    def test_unmodelled_message_not_recorded(self, fake_mido: _FakeMido) -> None:
        # ``_on_message`` early-returns on a message ``_convert`` drops
        # (pitch-bend, sysex, …) before reaching the ring.
        sub, port = self._open(fake_mido)
        port.callback(_FakeMessage(type="pitchwheel"))
        assert sub.recent_events() == []

    def test_evicts_oldest_at_capacity(
        self,
        fake_mido: _FakeMido,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Patch the capacity before construction (the deque reads the
        # module global at ``__init__`` time) so eviction is exercised
        # without pumping 100 messages.
        monkeypatch.setattr(midi_mod, "_EVENT_RING_CAPACITY", 3)
        sub, port = self._open(fake_mido)
        for note in (1, 2, 3, 4, 5):
            port.callback(_FakeMessage(type="note_on", note=note, velocity=1))
        notes = [e.number for e in sub.recent_events()]
        assert notes == [3, 4, 5]  # oldest (1, 2) evicted

    def test_snapshot_is_defensive_copy(self, fake_mido: _FakeMido) -> None:
        sub, port = self._open(fake_mido)
        port.callback(_FakeMessage(type="note_on", note=60, velocity=1))
        snapshot = sub.recent_events()
        snapshot.clear()
        assert len(sub.recent_events()) == 1  # internal ring untouched


# ---------------------------------------------------------------------------
# MIDI Learn capture
# ---------------------------------------------------------------------------


class TestArmCapture:
    def test_returns_next_event(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        port = fake_mido.opened[0]
        assert port.callback is not None
        captured: list[MidiEvent | None] = []

        def arm_in_thread() -> None:
            captured.append(sub.arm_capture(timeout_s=2.0))

        t = threading.Thread(target=arm_in_thread)
        t.start()
        # Give the arm thread time to install its event before pumping.
        time.sleep(0.05)
        port.callback(_FakeMessage(type="note_on", note=64, velocity=100))
        t.join(timeout=2.0)
        assert len(captured) == 1
        evt = captured[0]
        assert evt is not None and evt.type == "note_on" and evt.number == 64

    def test_returns_none_on_timeout(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        sub = MidiSubsystem()
        result = sub.arm_capture(timeout_s=0.05)
        assert result is None

    def test_only_first_event_captured_per_arm(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        port = fake_mido.opened[0]
        assert port.callback is not None
        captured: list[MidiEvent | None] = []

        def arm_in_thread() -> None:
            captured.append(sub.arm_capture(timeout_s=1.0))

        t = threading.Thread(target=arm_in_thread)
        t.start()
        time.sleep(0.05)
        # Two events in rapid succession; only the first should be captured.
        port.callback(_FakeMessage(type="control_change", control=10, value=64))
        port.callback(_FakeMessage(type="control_change", control=11, value=70))
        t.join(timeout=1.0)
        assert len(captured) == 1
        evt = captured[0]
        assert evt is not None and evt.number == 10  # not 11

    def test_arm_clears_stale_slot(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        sub = MidiSubsystem()
        # First arm times out – slot should be reset on next arm.
        assert sub.arm_capture(timeout_s=0.01) is None
        # Re-arm cleanly returns None (no leak from previous state).
        assert sub.arm_capture(timeout_s=0.01) is None


# ---------------------------------------------------------------------------
# Conversion: every supported mido type plus a few rejected ones
# ---------------------------------------------------------------------------


class TestConvert:
    def test_note_on(self) -> None:
        evt = MidiSubsystem._convert(
            1,
            _FakeMessage(type="note_on", channel=2, note=60, velocity=100),
        )
        assert evt is not None
        assert evt.type == "note_on" and evt.channel == 3
        assert evt.number == 60 and evt.value == 100
        assert evt.patch_id == 1

    def test_note_on_velocity_zero_rewrites_to_note_off(self) -> None:
        # Many MIDI controllers encode key release as ``note_on
        # velocity=0`` instead of emitting ``note_off``; normalize it to
        # standard ``note_off``.
        evt = MidiSubsystem._convert(
            1,
            _FakeMessage(type="note_on", channel=0, note=60, velocity=0),
        )
        assert evt is not None
        assert evt.type == "note_off"
        assert evt.number == 60 and evt.value == 0

    def test_note_off(self) -> None:
        evt = MidiSubsystem._convert(
            1,
            _FakeMessage(type="note_off", channel=0, note=60, velocity=0),
        )
        assert evt is not None
        assert evt.type == "note_off" and evt.value == 0

    def test_control_change(self) -> None:
        evt = MidiSubsystem._convert(
            1,
            _FakeMessage(type="control_change", channel=0, control=7, value=64),
        )
        assert evt is not None
        assert evt.type == "control_change" and evt.number == 7 and evt.value == 64

    def test_program_change(self) -> None:
        evt = MidiSubsystem._convert(
            1,
            _FakeMessage(type="program_change", channel=0, program=12),
        )
        assert evt is not None
        assert evt.type == "program_change"
        assert evt.number is None and evt.value == 12

    def test_polytouch_maps_to_key_pressure(self) -> None:
        evt = MidiSubsystem._convert(
            1,
            _FakeMessage(type="polytouch", channel=0, note=60, value=80),
        )
        assert evt is not None
        assert evt.type == "key_pressure"
        assert evt.number == 60 and evt.value == 80

    def test_aftertouch_maps_to_channel_pressure(self) -> None:
        evt = MidiSubsystem._convert(
            1,
            _FakeMessage(type="aftertouch", channel=0, value=90),
        )
        assert evt is not None
        assert evt.type == "channel_pressure"
        assert evt.number is None and evt.value == 90

    def test_unknown_type_returns_none(self) -> None:
        # mido types we don't model – pitch_bend, sysex, clock, etc.
        evt = MidiSubsystem._convert(1, _FakeMessage(type="pitchwheel"))
        assert evt is None

    def test_unknown_type_dispatch_drops_event(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        # Dispatch path also drops un-modelled types – covers the
        # ``if event is None`` early-return inside ``_on_message``.
        fake_mido.input_names = ["MIDI Mix"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
            ]
        )
        received: list[MidiEvent] = []
        sub.subscribe(received.append)
        port = fake_mido.opened[0]
        assert port.callback is not None
        port.callback(_FakeMessage(type="pitchwheel"))
        assert received == []


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_closes_every_open_port(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        fake_mido.input_names = ["MIDI Mix", "X-Touch Mini"]
        sub = MidiSubsystem()
        sub.apply_config(
            [
                MidiPatch(
                    id=1,
                    alias="A",
                    port_name="MIDI Mix",
                    product="MIDI Mix",
                ),
                MidiPatch(
                    id=2,
                    alias="B",
                    port_name="X-Touch Mini",
                    product="X-Touch Mini",
                ),
            ]
        )
        sub.shutdown()
        assert all(p.closed for p in fake_mido.opened)

    def test_clears_subscribers(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        sub = MidiSubsystem()
        sub.subscribe(lambda _e: None)
        sub.shutdown()
        # No formal API to inspect, but a fresh apply_config + dispatch
        # would no-op if subscribers were leaked. Easiest assertion:
        # internal list is empty.
        assert sub._subscribers == []

    def test_idempotent(
        self,
        fake_mido: _FakeMido,
    ) -> None:
        sub = MidiSubsystem()
        sub.shutdown()
        sub.shutdown()  # second shutdown must not raise
