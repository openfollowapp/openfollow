# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""End-to-end MIDI patch hot-reload integration test.

The full stack – :class:`MidiSubsystem` substrate +
:class:`OscTransmitterManager` dispatch + :class:`MidiMessageTrigger`
row – needs to keep working when the operator hot-reloads a
device's underlying port mapping. The integer patch ``id`` is the
stable foreign key the trigger rows reference; ``port_name`` changes
(e.g. the operator unplugged the device from one USB port and plugged
it into another) must not break dispatch as long as the patch ``id``
stays the same. The substrate's ``apply_config`` closes the old port
and opens the new one without changing the patch id the subscribers
see.

Driven against an in-memory fake mido + fake :class:`OscService`
so the test runs without any real rtmidi backend or network I/O,
and the hot-reload step is a single ``apply_config`` call rather
than a full configuration round-trip.
"""

from __future__ import annotations

import threading

import pytest

from openfollow.configuration import (
    MidiMessageTrigger,
    MidiPatch,
    OscDestinationConfig,
    OscDestinationsConfig,
    OscTransmitterConfig,
    OscTransmittersConfig,
)
from openfollow.input import midi as midi_mod
from openfollow.input.midi import MidiSubsystem
from openfollow.osc.transmitter import OscTransmitterManager

# Shared mido / OSC harness with test_input_midi.py to avoid drift between test suites.
from tests._fake_midi import (
    FakeMessage as _FakeMessage,
)
from tests._fake_midi import (
    FakeMido as _FakeMido,
)
from tests._fake_midi import (
    FakeOscService as _FakeOscService,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_mido(monkeypatch: pytest.MonkeyPatch) -> _FakeMido:
    fake = _FakeMido()
    monkeypatch.setattr(midi_mod, "_mido", fake)
    monkeypatch.setattr(midi_mod, "_MIDO_IMPORT_ERROR", None)
    return fake


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def _build_stack(
    *,
    patches: list[MidiPatch],
    triggers: list[OscTransmitterConfig],
) -> tuple[MidiSubsystem, OscTransmitterManager, _FakeOscService]:
    """Wire the subsystem + manager + fake OSC service the same way
    :class:`AppRuntimeServices.init_osc_transmitters` does – without
    spinning up the rest of the runtime. Returns the trio so the
    test can pump events + assert on sends.
    """
    midi = MidiSubsystem()
    midi.apply_config(patches)
    service = _FakeOscService()
    manager = OscTransmitterManager(
        osc_service=service,  # type: ignore[arg-type]
        marker_provider=lambda _tid: None,
        grid_provider=lambda: (10.0, 6.0, 0.0, 0.0),
    )
    manager.restart(
        OscTransmittersConfig(transmitters=triggers),
        OscDestinationsConfig(destinations=[OscDestinationConfig(id="d1", host="127.0.0.1", port=9000)]),
    )
    manager.attach_midi_subsystem(midi)
    return midi, manager, service


def test_midi_event_dispatches_through_patch_after_port_rename(
    fake_mido: _FakeMido,
) -> None:
    """The integration scenario: an operator
    moves the device to a different USB port (``port_name`` changes,
    patch ``id`` stays). The substrate closes the old port + opens the
    new one without changing the patch id the subscribers see; trigger
    rows that bind by patch id keep dispatching through the new port.

    This would be flaky if the substrate had stored
    callbacks keyed by ``port_name`` instead of by patch id – moving
    the device would have orphaned the subscription. The
    ``patch id`` → ``_OpenPort`` mapping in
    :class:`MidiSubsystem._open_ports` keeps the patch id as the
    stable foreign key precisely so this scenario works.
    """
    # ---- Initial state: device at "MIDI Mix"; patch id 1
    fake_mido.input_names = ["MIDI Mix"]
    patches = [
        MidiPatch(
            id=1,
            alias="Workspace 1",
            port_name="MIDI Mix",
            product="MIDI Mix",
        )
    ]
    trigger_row = OscTransmitterConfig(
        id="row-1",
        enabled=True,
        destination_id="d1",
        markers=[],
        address="/cc/[value]",
        args=[],
        trigger=MidiMessageTrigger(
            patch_id=1,
            type="control_change",
            channel=1,
            number=7,
        ),
    )
    midi, manager, service = _build_stack(
        patches=patches,
        triggers=[trigger_row],
    )

    # ---- Pump an event through the original port.
    initial_port = fake_mido.opened[-1]
    assert initial_port.callback is not None
    initial_port.callback(
        _FakeMessage(
            type="control_change",
            channel=0,
            control=7,
            value=42,
        )
    )
    manager.process_pending_events()
    # The trigger row matched and the rendered address is on the wire.
    assert len(service.calls) == 1
    assert service.calls[0][0] == "/cc/42"

    # ---- Hot-reload: device replugged on a different USB port. The
    # patch id stays 1; only ``port_name`` / ``product`` change. The
    # substrate must close the old port and open the new one under the
    # same patch id.
    fake_mido.input_names = ["MIDI Mix Mk2"]
    midi.apply_config(
        [
            MidiPatch(
                id=1,
                alias="Workspace 1",
                port_name="MIDI Mix Mk2",
                product="MIDI Mix Mk2",
            )
        ]
    )
    # The original port closes.
    assert initial_port.closed is True
    # A fresh port opens at the new name.
    new_port = fake_mido.opened[-1]
    assert new_port.name == "MIDI Mix Mk2"
    assert new_port.callback is not None
    assert new_port is not initial_port

    # ---- Pump an event through the new port. Same patch id →
    # same trigger row → same dispatch. The send count grows.
    new_port.callback(
        _FakeMessage(
            type="control_change",
            channel=0,
            control=7,
            value=99,
        )
    )
    manager.process_pending_events()
    assert len(service.calls) == 2
    assert service.calls[1][0] == "/cc/99"


def test_late_event_on_closed_port_callback_does_not_double_dispatch(
    fake_mido: _FakeMido,
) -> None:
    """In production the rtmidi backend stops firing the closed
    port's callback after ``close()``; the substrate doesn't gate
    on ``port.closed`` itself. But suppose rtmidi misbehaves and
    fires one stray late event on the OLD callback after the
    hot-reload. The patch-id-keyed dispatch path means that stray
    event still tags with the SAME patch id the new port now uses –
    so the trigger row matches and produces ONE send. The
    contract this test pins down: no DUPLICATE send. If the
    subsystem accidentally fanned events from BOTH the closed and
    new ports for the same patch id, a single pumped event would
    trip the dispatcher twice.

    Concretely: pumping one event via the OLD callback yields
    exactly one send (not two), and a subsequent event on the
    NEW callback also yields exactly one send. The patch-id-keyed
    mapping is the single source of truth – there's no
    duplicate-dispatch hazard from a stray late callback.
    """
    fake_mido.input_names = ["MIDI Mix"]
    patches = [
        MidiPatch(
            id=1,
            alias="Workspace 1",
            port_name="MIDI Mix",
            product="MIDI Mix",
        )
    ]
    trigger_row = OscTransmitterConfig(
        id="row-1",
        enabled=True,
        destination_id="d1",
        markers=[],
        address="/cc/[value]",
        args=[],
        trigger=MidiMessageTrigger(
            patch_id=1,
            type="control_change",
            channel=1,
            number=7,
        ),
    )
    midi, manager, service = _build_stack(
        patches=patches,
        triggers=[trigger_row],
    )
    initial_port = fake_mido.opened[-1]
    initial_callback = initial_port.callback
    assert initial_callback is not None

    # Hot-reload to a new port. The substrate closes the original.
    fake_mido.input_names = ["MIDI Mix Mk2"]
    midi.apply_config(
        [
            MidiPatch(
                id=1,
                alias="Workspace 1",
                port_name="MIDI Mix Mk2",
                product="MIDI Mix Mk2",
            )
        ]
    )
    assert initial_port.closed is True
    new_port = fake_mido.opened[-1]

    # Stray event on the OLD callback – the closure still references
    # patch id 1 so the substrate would dispatch it if rtmidi were
    # misbehaving. The dispatcher runs through the SAME patch-id-keyed
    # match logic and produces ONE send; if the subsystem accidentally
    # fanned events from the closed port AND the new port, we'd see
    # two sends per pumped event.
    initial_callback(
        _FakeMessage(
            type="control_change",
            channel=0,
            control=7,
            value=10,
        )
    )
    manager.process_pending_events()
    assert len(service.calls) == 1
    # New port also still works.
    assert new_port.callback is not None
    new_port.callback(
        _FakeMessage(
            type="control_change",
            channel=0,
            control=7,
            value=20,
        )
    )
    manager.process_pending_events()
    assert len(service.calls) == 2


def test_midi_patch_reassignment_breaks_old_trigger_row(
    fake_mido: _FakeMido,
) -> None:
    """Documents the expected limitation: when the operator re-keys
    the device onto a different patch ``id`` (not just renaming the
    underlying port or alias), trigger rows that referenced the old
    patch id silently stop matching. This is the operator's workflow –
    they re-key the patch AND update affected trigger rows together; the
    system doesn't auto-rewrite trigger rows on patch-id edits.

    A future "patch reassignment propagation" pass would change this
    test to assert the opposite (trigger row's ``patch_id`` follows
    the move); landing it as a documented limitation today gives that
    follow-up a clear regression target."""
    fake_mido.input_names = ["MIDI Mix"]
    patches = [
        MidiPatch(
            id=1,
            alias="Workspace 1",
            port_name="MIDI Mix",
            product="MIDI Mix",
        )
    ]
    trigger_row = OscTransmitterConfig(
        id="row-1",
        enabled=True,
        destination_id="d1",
        markers=[],
        address="/cc/[value]",
        args=[],
        trigger=MidiMessageTrigger(
            patch_id=1,
            type="control_change",
            channel=1,
            number=7,
        ),
    )
    midi, manager, service = _build_stack(
        patches=patches,
        triggers=[trigger_row],
    )

    # Operator re-keys the device from patch id 1 to patch id 2.
    # The substrate sees a different patch-id key – drops the
    # id-1 port and opens a fresh one under id 2.
    midi.apply_config(
        [
            MidiPatch(
                id=2,
                alias="Master",
                port_name="MIDI Mix",
                product="MIDI Mix",
            )
        ]
    )
    new_port = fake_mido.opened[-1]
    assert new_port.callback is not None

    # Pump an event – the substrate tags it with the new patch id
    # (2) but the trigger row still expects patch id 1.
    # No match → no send.
    new_port.callback(
        _FakeMessage(
            type="control_change",
            channel=0,
            control=7,
            value=42,
        )
    )
    manager.process_pending_events()
    assert service.calls == []


def test_concurrent_event_during_apply_config_does_not_race(
    fake_mido: _FakeMido,
) -> None:
    """The substrate's apply_config takes the subsystem lock; an
    event landing on rtmidi's listener thread mid-reload waits on
    the same lock before reaching ``_on_message``'s subscriber
    fan-out. This test fires an event from a worker thread while
    the main thread runs apply_config; the event must dispatch
    cleanly under either ordering without raising or duplicating
    sends.

    The lock contract is internal to ``MidiSubsystem`` (the test
    relies on it but doesn't poke at the lock directly); the
    integration assertion is "no exception, no duplicate send"."""
    fake_mido.input_names = ["MIDI Mix"]
    patches = [
        MidiPatch(
            id=1,
            alias="Workspace 1",
            port_name="MIDI Mix",
            product="MIDI Mix",
        )
    ]
    trigger_row = OscTransmitterConfig(
        id="row-1",
        enabled=True,
        destination_id="d1",
        markers=[],
        address="/cc/[value]",
        args=[],
        trigger=MidiMessageTrigger(
            patch_id=1,
            type="control_change",
            channel=1,
            number=7,
        ),
    )
    midi, manager, service = _build_stack(
        patches=patches,
        triggers=[trigger_row],
    )
    port = fake_mido.opened[-1]
    assert port.callback is not None

    # Barrier guarantees the pumper and the apply_config caller
    # both pass through a synchronisation point before either
    # starts its real work – without this the pumper could finish
    # all 20 callbacks before the main thread even attempts to
    # acquire the substrate lock, and the lock-contention path
    # this test exists to exercise wouldn't actually fire.
    start_barrier = threading.Barrier(2)
    pump_done = threading.Event()
    pump_error: list[BaseException] = []

    def _pump() -> None:
        try:
            start_barrier.wait(timeout=5.0)
            for _ in range(20):
                port.callback(
                    _FakeMessage(
                        type="control_change",
                        channel=0,
                        control=7,
                        value=1,
                    )
                )
        except BaseException as exc:  # pragma: no cover - defensive
            pump_error.append(exc)
        finally:
            pump_done.set()

    pumper = threading.Thread(target=_pump)
    pumper.start()
    start_barrier.wait(timeout=5.0)
    # Re-apply the same config (still a real lock acquisition; the
    # substrate's apply_config is idempotent so this exercises the
    # lock-contention path without changing the visible state).
    # With both threads released from the barrier simultaneously,
    # the pumper's _on_message lock acquisitions race against the
    # apply_config lock acquisition.
    midi.apply_config(patches)
    pumper.join(timeout=5.0)
    assert pump_done.is_set()
    assert pump_error == []
    manager.process_pending_events()
    # 20 events pumped → 20 sends. No duplicates, no drops.
    assert len(service.calls) == 20
