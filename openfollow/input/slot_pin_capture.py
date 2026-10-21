# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Waiting for the button press that names the controller to pin to a slot.

Pure logic over what the handlers report each frame: pads as the raw buttons
held now, 3D mice as the presses latched since the last read plus what is held.
A press is an input that was not held when the capture armed, so a button
already down then (the one that started it, on a pad) never counts. After the
press the capture waits for its release, so the press reaches no other consumer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# How long Pin here waits for a press.
CAPTURE_TIMEOUT_S = 10.0
# How long a press may stay held before the capture ends anyway.
RELEASE_TIMEOUT_S = 2.0

WAITING = "waiting"
PRESSED = "pressed"
TIMED_OUT = "timed_out"

# Outcomes. Published through /api/stats, so the values are a wire interface.
PINNED = "pinned"
NO_PORT = "no_port"
NO_SLOT = "no_slot"
TIMEOUT = "timeout"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class CaptureResult:
    """How a capture for ``slot`` (0-based) ended; ``detail`` names the socket or the controller."""

    slot: int
    outcome: str
    detail: str = ""


class PinCapture:
    """One armed Pin here. Feed it every frame with :meth:`step` until it stops waiting."""

    def __init__(self, slot: int, *, now: float, pad_held: Mapping[int, frozenset[int]]):
        self.slot = slot
        self.started = now
        self._baseline: dict[int, set[int]] = {idx: set(held) for idx, held in pad_held.items()}
        self.pressed: tuple[str, int] | None = None
        self._pressed_at = 0.0

    def seconds_left(self, now: float) -> float:
        return max(0.0, CAPTURE_TIMEOUT_S - (now - self.started))

    def step(
        self,
        now: float,
        pad_held: Mapping[int, frozenset[int]],
        puck_presses: Mapping[int, set[int]],
        puck_held: Mapping[int, frozenset[int]],
    ) -> str:
        """``WAITING``, ``PRESSED`` once a press has been released, or ``TIMED_OUT``."""
        if self.pressed is None:
            if now - self.started >= CAPTURE_TIMEOUT_S:
                return TIMED_OUT
            self.pressed = self._first_press(pad_held, puck_presses)
            if self.pressed is None:
                return WAITING
            self._pressed_at = now
        kind, local_id = self.pressed
        if kind == "gamepad":
            still = pad_held.get(local_id, frozenset()) - self._baseline.get(local_id, set())
        else:
            still = puck_held.get(local_id, frozenset())
        if still and now - self._pressed_at < RELEASE_TIMEOUT_S:
            return WAITING
        return PRESSED

    def _first_press(
        self, pad_held: Mapping[int, frozenset[int]], puck_presses: Mapping[int, set[int]]
    ) -> tuple[str, int] | None:
        for idx in sorted(pad_held):
            held = pad_held[idx]
            # A pad plugged in mid-capture starts from what it holds.
            baseline = self._baseline.setdefault(idx, set(held))
            # A baseline button let go of counts when pressed again.
            baseline &= held
            if held - baseline:
                return ("gamepad", idx)
        for idx in sorted(puck_presses):
            if puck_presses[idx]:
                return ("mouse3d", idx)
        return None
