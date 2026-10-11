# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Stand-ins for a ``threading.Thread`` factory."""

from __future__ import annotations

from collections.abc import Callable


class InlineThread:
    """Runs its target at ``start()``, so a background read has finished by the
    time the call that started it returns."""

    def __init__(self, *, target: Callable[..., object], args: tuple[object, ...] = (), **_kwargs: object) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)


class HeldThreads:
    """A thread factory whose threads never run, so every read stays in flight. Counts starts."""

    def __init__(self) -> None:
        self.started = 0

    def __call__(self, **_kwargs: object) -> HeldThreads:
        return self

    def start(self) -> None:
        self.started += 1
