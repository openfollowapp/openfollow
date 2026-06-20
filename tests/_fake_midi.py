# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared fake ``mido`` substrate for MIDI tests.

Both unit and integration test suites drive :class:`MidiSubsystem` without
real rtmidi backend. Shared module keeps contract uniform across suites.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass
class FakeMessage:
    """Stand-in for ``mido.Message``. Only the attributes the
    subsystem reads are populated; the rest of mido's surface is
    irrelevant to conversion."""

    type: str
    channel: int = 0  # mido is 0-indexed; subsystem converts to 1-16
    note: int = 0
    velocity: int = 0
    control: int = 0
    value: int = 0
    program: int = 0


class FakePort:
    """Minimal ``mido`` InputPort substitute.

    The subsystem attaches a callback via ``port.callback = ...``;
    tests pump synthetic messages by invoking ``self.callback(msg)``
    directly (mido does this from its private listener thread; we
    can do it inline because :meth:`MidiSubsystem._on_message` is
    thread-safe under its own lock).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.callback: Callable[[Any], None] | None = None
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeMido:
    """Module-level fake replacing :mod:`mido` for one test.

    ``input_names`` scripts the next ``get_input_names()`` return
    value; ``opened`` records every port the subsystem opens so
    tests can assert on lifecycle (open / close / replace).

    ``discover_raises`` makes ``get_input_names`` raise – exercises
    the subsystem's "backend failure during discover" path.
    ``open_raises_for`` is the set of port names whose
    ``open_input`` call should raise – exercises the
    "alias matched but the port refuses to open" path.
    ``get_input_names_calls`` counts every enumeration attempt so a
    test can assert discovery was *skipped* (count stays 0) rather
    than inferring it from a side effect.
    """

    def __init__(
        self,
        input_names: list[str] | None = None,
    ) -> None:
        self.input_names: list[str] = input_names or []
        self.opened: list[FakePort] = []
        self.discover_raises: bool = False
        self.open_raises_for: set[str] = set()
        self.get_input_names_calls: int = 0

    def get_input_names(self) -> list[str]:
        self.get_input_names_calls += 1
        if self.discover_raises:
            raise RuntimeError("simulated discover failure")
        return list(self.input_names)

    def open_input(self, name: str) -> FakePort:
        if name in self.open_raises_for:
            raise RuntimeError(f"simulated open failure for {name}")
        port = FakePort(name)
        self.opened.append(port)
        return port


class FakeOscService:
    """Records every send for assertion. Doesn't open sockets.

    Mirrors the real :class:`openfollow.osc.service.OscService.send`
    signature so the OSC transmitter manager can call us without
    knowing the difference.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Any]]] = []

    def send(
        self,
        address: str,
        args: Sequence[Any] = (),
        *,
        host: str,
        port: int,
        protocol: str = "udp",
        framing: str = "slip",
    ) -> None:
        self.calls.append((address, list(args)))
